import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
import base64
import ctypes
import webbrowser
import urllib.request
import urllib.error
import json
import threading
import queue
import datetime
import time
import asyncio
import os
import uuid
import re
import math
from collections import deque
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from pathlib import Path

URL = 'https://api-v2.soundcloud.com/me/play-history/tracks?limit=1'

def token_expiry(token):
    try:
        part=token.strip().split()[-1].split('.')[1]
        return float(json.loads(base64.urlsafe_b64decode(part+'='*(-len(part)%4)))['exp'])
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        return None


def playback_bounds(played_at, duration, offset=0):
    """Return estimated start/end in Unix milliseconds, or None."""
    try:
        start=int(played_at)+int(offset)*1000
        length=int(duration)
        if start<=0 or length<=0: return None
        return start,start+length
    except (ValueError,TypeError,OverflowError): return None


def playback_state(bounds, now_ms, show_progress, clear_at_end, grace):
    # Discord may substitute its own elapsed counter if timestamps are absent.
    # Preserve the real start/end pair even after the estimated song ends.
    # Never invent a new start time for an old history entry.
    if bounds is None: return bool(show_progress),None
    start,end=bounds
    if clear_at_end and now_ms>=end+grace*1000: return True,None
    if show_progress: return False,{'start':start,'end':end}
    return False,None



def expand_template(text, values):
    # Optional groups: [%album% • ] disappear if any variable is empty.
    pattern=r'%([a-z_]+)%'
    def group(m):
        keys=re.findall(pattern,m[1])
        return m[1] if all(values.get(k) for k in keys) else ''
    text=re.sub(r'\[([^\[\]]*)\]',group,text)
    return re.sub(pattern,lambda m:str(values.get(m[1],m[0])),text).strip()


def music_activity(current, name, use_title, show_album, show_art, show_button, timestamps, template=None):
    key,title,artist,album,art,url,duration=current
    distinct=album if album and album.strip().casefold() not in (artist.strip().casefold(),title.strip().casefold()) else ''
    a={'name':(title if use_title else name)[:128] or 'SoundCloud','status_display_type':0,'type':2,'instance':False}
    a['details']=(artist if use_title else title)[:128]
    if use_title and show_album and distinct: a['state']=distinct[:128]
    elif not use_title: a['state']=artist[:128]
    if show_art and art and art.startswith('https://'):
        a['assets']={'large_image':art}
        if not use_title and show_album and distinct: a['assets']['large_text']=distinct[:128]
    if show_button and url and url.startswith('https://'): a['buttons']=[{'label':'Listen on SoundCloud','url':url}]
    if template is not None:
        try: seconds=max(0,int(duration)//1000)
        except (TypeError,ValueError): seconds=0
        values={'title':title,'artist':artist,'album':album,'distinct_album':distinct,'artwork':art or '', 'url':url or '', 'duration':'%d:%02d'%(seconds//60,seconds%60) if seconds else '', 'app':name or 'SoundCloud'}
        def expand(k):
            return expand_template(template.get(k,''),values)
        a['name']=expand('name')[:128] or 'SoundCloud'
        for k in ('details','state'):
            a.pop(k,None)
            if expand(k): a[k]=expand(k)[:128]
        a.pop('assets',None); assets={}
        for size in ('large','small'):
            image=expand(size+'_image')
            if size=='large' and not image: image=expand('fallback')
            if image and (image.startswith('https://') and urlsplit(image).netloc or re.fullmatch(r'[A-Za-z0-9_-]+',image)):
                assets[size+'_image']=image
                if expand(size+'_text'): assets[size+'_text']=expand(size+'_text')[:128]
        if assets: a['assets']=assets
        a.pop('buttons',None)
        for prefix in ('button','button2'):
            link=expand(prefix+'_url'); label=expand(prefix+'_label')
            if template.get(prefix+'_visible','Yes')=='Yes' and label and link.startswith(('https://','http://')) and urlsplit(link).netloc:
                a.setdefault('buttons',[]).append({'label':label[:32],'url':link})
        a['status_display_type']={'Activity name':0,'Details':1,'State':2}.get(template.get('status_display'),0)
    # Keep the required activity name and the higher-priority details line.
    # Compare whole visible fields only; do not alter distinct text or tooltips.
    seen={}
    for field, display_type in (('name',0),('details',1),('state',2)):
        value=' '.join(a.get(field,'').split()).casefold()
        if not value: continue
        if value in seen:
            a.pop(field,None)
            if a['status_display_type']==display_type:
                a['status_display_type']=seen[value]
        else:
            seen[value]=display_type
    if timestamps: a['timestamps']=timestamps
    return a

def protect(data, decrypt=False):
    # Windows DPAPI, bound to the current Windows user; no plaintext fallback.
    if os.name != 'nt':
        raise OSError('Windows DPAPI is unavailable')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]
    buf=ctypes.create_string_buffer(data)
    src=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)))
    dst=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    fn=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    fn.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p]; kernel.LocalFree.restype=ctypes.c_void_p
    if not fn(ctypes.byref(src),None,None,None,None,1,ctypes.byref(dst)):
        raise ctypes.WinError(ctypes.get_last_error())
    try: return ctypes.string_at(dst.pbData,dst.cbData)
    finally: kernel.LocalFree(ctypes.cast(dst.pbData,ctypes.c_void_p))


class LatestCommands:
    """Coalesce pending activity commands, never cross a connection barrier."""
    activity = frozenset(('update','custom','clear'))
    def __init__(self):
        from collections import deque
        self.items=deque(); self.cv=threading.Condition()
    def put(self, item):
        with self.cv:
            if item[0] in self.activity:
                while self.items and self.items[-1][0] in self.activity:
                    self.items.pop()
            elif item[0] in ('connect','disconnect','quit'):
                while self.items and self.items[-1][0] in self.activity:
                    self.items.pop()
            self.items.append(item); self.cv.notify()
    def get(self):
        with self.cv:
            while not self.items: self.cv.wait()
            return self.items.popleft()


class Tester:
    def __init__(self, root):
        self.root = root
        root.title('SoundCloud Presence | V0.28')
        root.geometry('900x850')
        root.minsize(760, 480)
        root.resizable(True, True)
        self.token = tk.StringVar()
        self.interval = tk.StringVar(value='20')
        self.status = tk.StringVar(value='Paused')
        self.track = tk.StringVar(value='No track retrieved')
        self.artist = tk.StringVar(value='')
        self.album = tk.StringVar(value='')
        self.art = tk.StringVar(value='')
        self.running = False
        self.busy = False
        self.timer = None
        self.last = None
        self.block_until = 0
        self.failures = 0
        self.results = queue.Queue()
        self.rpc_commands = LatestCommands()
        self.rpc_events = queue.Queue()
        self.app_id = tk.StringVar(value='')
        self.activity_name = tk.StringVar(value='SoundCloud')
        self.title_as_name = tk.BooleanVar(value=True)
        self.rpc_status = tk.StringVar(value='Discord disconnected')
        self.rpc_connected = False
        self.rpc_busy = False
        self.current = None
        self.published = None
        self.requested_presence = None
        self.rpc_sequence = 0
        self.rpc_last_ack = None
        self.detected_at = None
        self.rpc_enabled = True
        threading.Thread(target=self.rpc_worker, daemon=True).start()
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.show_progress = tk.BooleanVar(value=True)
        self.playback_offset = tk.StringVar(value='0')
        self.clear_at_end = tk.BooleanVar(value=False)
        self.end_grace = tk.StringVar(value='30')
        self.playback_label = tk.StringVar(value='Estimated playback unavailable')
        self.show_album = tk.BooleanVar(value=True)
        self.show_art = tk.BooleanVar(value=True)
        self.show_button = tk.BooleanVar(value=True)
        self.show_token = tk.BooleanVar(value=False)
        self.quiet = tk.BooleanVar(value=True)
        self.config_path = Path(os.environ.get('APPDATA', str(Path.home()))) / 'SoundCloudPresence' / 'settings.json'
        self.adaptive=tk.BooleanVar(value=True)
        self.daily_budget=tk.StringVar(value='8640')
        self.request_times=deque()
        self.limit_headers={}
        self.block_path=self.config_path.with_name('request_usage.json')
        self.restricted=False
        self.empty_seen=False
        self.load_usage()
        self.idle_minutes=tk.StringVar(value='10')
        self.idle_interval=tk.StringVar(value='60')
        self.last_change=time.monotonic()
        self.auth_status=tk.StringVar(value='No token')
        self.remember=tk.BooleanVar(value=True)
        self.pending_save=None
        self.resume_on_auth=False
        self.token_path=self.config_path.with_name('account.dpapi')
        self.generation=0
        self.rejected_token=None
        self.custom_mode=False
        self.music_was_running=False
        self.manual_queued=False
        self.request_manual=False
        self.custom_fields={k:tk.StringVar(value=v) for k,v in {
            'name':'My activity','details':'','state':'','large_image':'','large_text':'',
            'small_image':'','small_text':'','button_label':'','button_url':'','type':'Playing'}.items()}
        self.custom_elapsed=tk.BooleanVar(value=False)
        self.custom_started=None
        self.custom_activity=None
        self.mode_label=tk.StringVar(value='Music mode active')
        self.auto_reconnect=tk.BooleanVar(value=True)
        self.auto_connect=tk.BooleanVar(value=False)
        self.tray_enabled=tk.BooleanVar(value=False)
        self.expiry_notice=tk.BooleanVar(value=True)
        self.rpc_wanted=False
        self.rpc_retry_at=0
        self.last_success=None
        self.last_latency=None
        self.total_errors=0
        self.next_poll_at=None
        self.request_started=0
        self.notice_key=None
        self.presets={}
        self.preset_name=tk.StringVar()
        self.diagnostics=tk.StringVar(value='No requests yet')
        self.tray_events=queue.Queue()
        self.tray_icon=None
        self.closing=False
        self.template_enabled=tk.BooleanVar(value=False)
        self.template_defaults={'name':'%title%','details':'%artist%','state':'%distinct_album%','large_image':'%artwork%','large_text':'%album%','fallback':'','small_image':'','small_text':'','button_label':'Listen on SoundCloud','button_url':'%url%','status_display':'Activity name','button_visible':'Yes','button2_visible':'No','button2_label':'','button2_url':''}
        self.template_fields={k:tk.StringVar(value=v) for k,v in self.template_defaults.items()}
        self.applied_template=None
        self.music_presets={}
        self.music_preset_name=tk.StringVar()
        self.opt_gui=tk.BooleanVar(value=False)
        self.opt_idle_rpc=tk.BooleanVar(value=False)
        self.opt_background_preview=tk.BooleanVar(value=True)
        self.log_limit=tk.StringVar(value='1000')
        self.persist_log=tk.BooleanVar(value=True)
        self.preview_pending=False
        self.last_inactive_signature=None
        self.inactive_mode=tk.StringVar(value='Keep last song')
        self.inactive_delay=tk.StringVar(value='30')
        self.inactive_label=tk.StringVar(value='Listening inactive')
        self.log_categories=('New tracks','Unchanged polls','Discord','Account','Errors','Other')
        self.log_filters={k:tk.BooleanVar(value=True) for k in self.log_categories}
        self.log_records=deque(maxlen=3000)
        self.load_settings()
        self.applied_template=self.get_template()
        self.saved_template={k:v.get() for k,v in self.template_fields.items()}
        self.build_ui()
        self.root.report_callback_exception=self.callback_error
        self.restore_token()
        self.auth_tick()
        self.playback_tick()
        root.after(40,self.consume)
        root.after(500,self.maintenance)
        if self.auto_connect.get(): root.after(300,self.connect_on_open)
    def load_settings(self):
        try:
            data=json.loads(self.config_path.read_text(encoding='utf-8'))
            for name in ('app_id','activity_name','interval','title_as_name','show_album','show_art','show_button','quiet','adaptive','idle_minutes','idle_interval','show_progress','playback_offset','clear_at_end','end_grace','auto_reconnect','auto_connect','tray_enabled','expiry_notice','daily_budget','template_enabled','inactive_mode','inactive_delay','inactive_label','opt_gui','opt_idle_rpc','opt_background_preview','log_limit','persist_log'):
                if name in data: getattr(self,name).set(data[name])
            for k,v in data.get('custom_fields',{}).items():
                if k in self.custom_fields: self.custom_fields[k].set(v)
            for k,v in data.get('music_template',{}).items():
                if k in self.template_fields: self.template_fields[k].set(v)
            for k,v in data.get('log_filters',{}).items():
                if k in self.log_filters: self.log_filters[k].set(bool(v))
            self.music_presets={k:v for k,v in data.get('music_presets',{}).items() if isinstance(k,str) and isinstance(v,dict)}
            self.custom_elapsed.set(data.get('custom_elapsed',False))
            self.presets={k:v for k,v in data.get('presets',{}).items() if isinstance(k,str) and isinstance(v,dict)}
            if not self.interval.get().isdigit() or int(self.interval.get())<1: self.interval.set('20')
        except (OSError, ValueError, TypeError): pass
    def save_settings(self):
        try:
            self.config_path.parent.mkdir(parents=True,exist_ok=True)
            data={name:getattr(self,name).get() for name in ('app_id','activity_name','interval','title_as_name','show_album','show_art','show_button','quiet','adaptive','idle_minutes','idle_interval','show_progress','playback_offset','clear_at_end','end_grace','auto_reconnect','auto_connect','tray_enabled','expiry_notice','daily_budget','template_enabled','inactive_mode','inactive_delay','inactive_label','opt_gui','opt_idle_rpc','opt_background_preview','log_limit','persist_log')}
            data['music_template']=self.saved_template.copy()
            data['template_enabled']=self.applied_template is not None
            data['log_filters']={k:v.get() for k,v in self.log_filters.items()}
            data['custom_fields']={k:v.get() for k,v in self.custom_fields.items()}
            data['custom_elapsed']=self.custom_elapsed.get()
            data['presets']=self.presets
            data['music_presets']=self.music_presets
            self.config_path.write_text(json.dumps(data,indent=2),encoding='utf-8')
        except OSError: self.log('Could not save settings')
    def build_ui(self):
        bg='#14171c'; panel='#20242b'; fg='#edf0f4'; muted='#a1abb9'; accent='#ff7138'
        self.root.configure(bg=bg)
        style=ttk.Style(self.root); style.theme_use('clam')
        style.configure('.',background=bg,foreground=fg,font=('Segoe UI',10))
        style.configure('TFrame',background=bg)
        style.configure('Panel.TFrame',background=panel)
        style.configure('Panel.TLabel',background=panel,foreground=fg)
        style.configure('Muted.TLabel',background=bg,foreground=muted)
        style.configure('Title.TLabel',font=('Segoe UI',21,'bold'),foreground=fg)
        style.configure('Track.TLabel',background=panel,foreground=fg,font=('Segoe UI',19,'bold'))
        style.configure('TButton',background='#343b46',foreground=fg,borderwidth=0,padding=(12,8))
        style.map('TButton',background=[('active','#485364'),('pressed','#586477')])
        style.configure('Accent.TButton',background=accent,foreground='#161616',font=('Segoe UI',10,'bold'))
        style.map('Accent.TButton',background=[('active','#ff985f')])
        style.configure('TEntry',fieldbackground='#292f38',foreground=fg,insertcolor=fg,bordercolor='#414955',padding=6)
        style.map('TEntry',fieldbackground=[('readonly','#292f38')],foreground=[('readonly',fg)])
        style.configure('TCombobox',fieldbackground='#292f38',background='#343b46',foreground=fg,arrowcolor=fg,padding=5)
        style.map('TCombobox',fieldbackground=[('readonly','#292f38')],foreground=[('readonly',fg)])
        self.root.option_add('*TCombobox*Listbox.background','#292f38'); self.root.option_add('*TCombobox*Listbox.foreground',fg)
        style.configure('TCheckbutton',background=bg,foreground=fg,padding=4,indicatorbackground='#343b46')
        style.map('TCheckbutton',background=[('active',bg)],indicatorbackground=[('selected',accent)])
        style.configure('TNotebook',background=bg,borderwidth=0)
        style.configure('TNotebook.Tab',background='#292f38',foreground=muted,padding=(12,9))
        style.map('TNotebook.Tab',background=[('selected',panel)],foreground=[('selected',fg)])
        outer=ttk.Frame(self.root,padding=18); outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='SoundCloud Presence',style='Title.TLabel').pack(anchor='w')
        ttk.Label(outer,text='V0.28  /  Account playback history → Discord',style='Muted.TLabel').pack(anchor='w',pady=(0,12))
        bar=ttk.Frame(outer); bar.pack(fill='x',pady=(0,12))
        ttk.Label(bar,textvariable=self.status,foreground=accent,wraplength=460).pack(side='left')
        ttk.Label(bar,textvariable=self.rpc_status,style='Muted.TLabel').pack(side='right')
        tabs=ttk.Notebook(outer); tabs.pack(fill='both',expand=True)
        home=ttk.Frame(tabs,padding=14); settings=ttk.Frame(tabs,padding=14); account=ttk.Frame(tabs,padding=14); logs=ttk.Frame(tabs,padding=14)
        tabs.add(home,text='Now playing'); tabs.add(account,text='Accounts')
        settings_host=settings
        settings_nav=ttk.Notebook(settings_host); settings_nav.pack(fill='both',expand=True)
        settings=ttk.Frame(settings_nav,padding=8); timeline=ttk.Frame(settings_nav,padding=8); tools=ttk.Frame(settings_nav,padding=8); perf=ttk.Frame(settings_nav,padding=8)
        for page,label in [(timeline,'Playback & inactivity'),(settings,'Polling & API'),(tools,'Desktop & diagnostics'),(perf,'Performance')]: settings_nav.add(page,text=label)
        custom=ttk.Frame(tabs,padding=14)
        self.tabs=tabs; self.home_tab=home; self.custom_tab=custom; self.account_tab=account
        account= self.scroll_page(account); custom=self.scroll_page(custom)
        home=self.scroll_page(home)
        settings=self.scroll_page(settings); timeline=self.scroll_page(timeline)
        tools=self.scroll_page(tools); perf=self.scroll_page(perf)
        def heading(parent,text): ttk.Label(parent,text=text,font=('Segoe UI',12,'bold')).pack(anchor='w',pady=(8,5))
        def buttons(parent,items):
            r=ttk.Frame(parent); r.pack(fill='x',pady=6)
            for text,cmd,primary in items: ttk.Button(r,text=text,command=cmd,style='Accent.TButton' if primary else 'TButton').pack(side='left',padx=(0,6))
        def duration(parent,var,presets,base=1,minimum=0,signed=False):
            self.duration_control(parent,var,presets,base,minimum,signed)
        card=ttk.Frame(home,style='Panel.TFrame',padding=20); card.pack(fill='x',pady=(0,12))
        ttk.Label(card,text='LATEST DETECTED TRACK',style='Panel.TLabel',foreground=muted).pack(anchor='w')
        ttk.Label(card,textvariable=self.track,style='Track.TLabel',wraplength=600).pack(anchor='w',pady=(8,4))
        ttk.Label(card,textvariable=self.artist,style='Panel.TLabel',font=('Segoe UI',12)).pack(anchor='w')
        ttk.Label(card,textvariable=self.album,style='Panel.TLabel',foreground=muted,wraplength=600).pack(anchor='w',pady=(3,6))
        ttk.Label(card,textvariable=self.playback_label,style='Panel.TLabel',foreground=muted).pack(anchor='w',pady=(4,2))
        heading(home,'Playback controls')
        buttons(home,[('Start / Resume',self.start,True),('Pause',self.pause,False),('Refresh now',self.manual_refresh,False)])
        heading(home,'Discord presence')
        ttk.Label(home,text='Configure both services in Accounts.',style='Muted.TLabel').pack(anchor='w')
        buttons(home,[('Publish latest',self.rpc_publish,True),('Clear',self.rpc_clear,False),('Accounts',lambda:self.tabs.select(self.account_tab),False)])
        heading(home,'Presence mode')
        ttk.Label(home,textvariable=self.mode_label).pack(anchor='w')
        buttons(home,[('Switch to custom presence',self.enable_custom,False)])
        heading(home,'Artwork URL')
        ttk.Entry(home,textvariable=self.art,state='readonly').pack(fill='x')
        ttk.Label(home,text='Playback is inferred from account history; pause and stop on your phone are not detected.',style='Muted.TLabel',wraplength=650).pack(anchor='w',pady=12)
        heading(custom,'Custom Discord presence')
        ttk.Label(custom,textvariable=self.mode_label).pack(anchor='w')
        buttons(custom,[('Enable custom mode',self.enable_custom,True),('Return to music',self.disable_custom,False)])
        ttk.Label(custom,text='Music polling is suspended in custom mode. Enter an uploaded Discord asset key or a public HTTPS image URL. Local image files are not supported by Discord RPC.',style='Muted.TLabel',wraplength=720).pack(anchor='w',pady=(0,8))
        for key,label in [('name','Activity name'),('details','First line'),('state','Second line'),('large_image','Large image URL / key'),('large_text','Large image tooltip'),('small_image','Small image URL / key'),('small_text','Small image tooltip'),('button_label','Button label'),('button_url','Button URL')]:
            r=ttk.Frame(custom); r.pack(fill='x',pady=2)
            ttk.Label(r,text=label,width=23).pack(side='left'); ttk.Entry(r,textvariable=self.custom_fields[key]).pack(side='left',fill='x',expand=True)
        r=ttk.Frame(custom); r.pack(fill='x',pady=4)
        ttk.Label(r,text='Activity type',width=23).pack(side='left')
        ttk.Combobox(r,textvariable=self.custom_fields['type'],values=('Playing','Listening','Watching','Competing'),state='readonly',width=15).pack(side='left')
        ttk.Checkbutton(custom,text='Show elapsed time',variable=self.custom_elapsed).pack(anchor='w')
        buttons(custom,[('Publish custom',self.publish_custom,True),('Clear',self.rpc_clear,False),('Save settings',self.save_settings,False)])
        buttons(custom,[('Connect / Switch',self.rpc_connect,False),('Disconnect',self.rpc_disconnect,False)])
        heading(custom,'Saved presets')
        r=ttk.Frame(custom); r.pack(fill='x',pady=3)
        self.preset_combo=ttk.Combobox(r,textvariable=self.preset_name,values=sorted(self.presets),width=22,state='readonly'); self.preset_combo.pack(side='left',fill='x',expand=True)
        for label,cmd in [('Load',self.load_preset),('Save as',self.save_preset),('Delete',self.delete_preset)]:
            ttk.Button(r,text=label,command=cmd).pack(side='left',padx=3)
        heading(account,'SoundCloud access token')
        ttk.Label(account,text='Paste your current oauth_token below. No browser extension required.',style='Muted.TLabel',wraplength=650).pack(anchor='w',pady=(0,8))
        r=ttk.Frame(account); r.pack(fill='x')
        self.token_entry=ttk.Entry(r,textvariable=self.token,show='*'); self.token_entry.pack(side='left',fill='x',expand=True)
        ttk.Checkbutton(r,text='Show',variable=self.show_token,command=lambda:self.token_entry.configure(show='' if self.show_token.get() else '*')).pack(side='left',padx=8)
        ttk.Label(account,textvariable=self.auth_status,style='Muted.TLabel',wraplength=650).pack(anchor='w',pady=8)
        ttk.Checkbutton(account,text='Remember verified token securely on this PC',variable=self.remember).pack(anchor='w')
        buttons(account,[('Connect / Save',self.connect_account,True),('Help: Get token',self.show_connection_guide,False),('Forget account',self.forget_account,False)])
        ttk.Label(account,text='Remembered access tokens are encrypted for your Windows account. Renewal is manual for now; no refresh token or password is stored.',style='Muted.TLabel',wraplength=650).pack(anchor='w',pady=8)
        heading(account,'Discord application')
        r=ttk.Frame(account); r.pack(fill='x'); ttk.Label(r,text='Application ID',width=18).pack(side='left'); ttk.Entry(r,textvariable=self.app_id).pack(side='left',fill='x',expand=True)
        ttk.Label(account,textvariable=self.rpc_status,style='Muted.TLabel').pack(anchor='w',pady=5)
        buttons(account,[('Connect / Switch',self.rpc_connect,True),('Disconnect',self.rpc_disconnect,False),('Developer portal',lambda:webbrowser.open('https://discord.com/developers/applications'),False)])
        ttk.Label(account,text='Enter your Discord application ID, then connect. The desktop Discord client must be running.',style='Muted.TLabel',wraplength=650).pack(anchor='w')
        heading(account,'Connection preferences')
        for label,var in [('Reconnect Discord automatically after a connection failure',self.auto_reconnect),('Connect services when I manually open this application',self.auto_connect),('Warn 30 minutes before token expiration',self.expiry_notice)]:
            ttk.Checkbutton(account,text=label,variable=var).pack(anchor='w')
        buttons(account,[('Save preferences',self.save_settings,False)])
        heading(settings,'Polling')
        r=ttk.Frame(settings); r.pack(fill='x',pady=5)
        ttk.Label(r,text='Poll every',width=24).pack(side='left'); duration(r,self.interval,(5,10,15,20,30,60,120),minimum=1)
        ttk.Checkbutton(settings,text='Reduce polling during inactivity',variable=self.adaptive).pack(anchor='w')
        r=ttk.Frame(settings); r.pack(fill='x',pady=4)
        ttk.Label(r,text='After inactivity of',width=24).pack(side='left'); duration(r,self.idle_minutes,(5,10,15,30,60),base=60,minimum=1)
        r=ttk.Frame(settings); r.pack(fill='x',pady=4)
        ttk.Label(r,text='Then poll every',width=24).pack(side='left'); duration(r,self.idle_interval,(60,120,180,300),minimum=1)
        heading(settings,'SoundCloud request protection')
        r=ttk.Frame(settings); r.pack(fill='x',pady=4)
        ttk.Label(r,text='Rolling 24-hour request budget (0 = unlimited)').pack(side='left')
        ttk.Entry(r,textvariable=self.daily_budget,width=9).pack(side='left',padx=8)
        ttk.Button(r,text='Save',command=self.save_budget).pack(side='left')
        ttk.Label(settings,text='5-second polling is experimental; the private endpoint has no verified safe limit. Cooldowns apply to manual requests too.',style='Muted.TLabel',wraplength=680).pack(anchor='w')
        buttons(settings,[('Save & apply',self.apply_settings,True)])
        heading(tools,'Desktop behavior')
        for label,var in [('Close button minimizes to system tray',self.tray_enabled)]:
            ttk.Checkbutton(tools,text=label,variable=var).pack(anchor='w')
        buttons(tools,[('Save settings',self.save_settings,True),('Hide to tray',self.hide_to_tray,False),('Exit application',self.exit_app,False)])
        heading(perf,'Optional performance optimizations')
        for label,var in [('Skip unchanged GUI values',self.opt_gui),('Skip repeated inactive Discord checks (experimental)',self.opt_idle_rpc),('Render preview only when Appearance is visible',self.opt_background_preview)]:
            ttk.Checkbutton(perf,text=label,variable=var).pack(anchor='w')
        buttons(perf,[('Save performance settings',self.save_settings,True)])
        heading(tools,'Connection diagnostics')
        ttk.Label(tools,textvariable=self.diagnostics,justify='left',wraplength=700).pack(anchor='w',pady=8)
        ttk.Label(tools,text='No Windows startup registration is used. Tray support is optional; if unavailable the window remains accessible.',style='Muted.TLabel',wraplength=650).pack(anchor='w')
        buttons(tools,[('Export settings',self.export_settings,False),('Open log folder',self.open_log_folder,False)])
        heading(timeline,'Estimated playback timeline')
        ttk.Checkbutton(timeline,text='Show playback progress on Discord',variable=self.show_progress).pack(anchor='w')
        ttk.Label(timeline,text='Uses SoundCloud history timestamp and track duration, not a live player position.',style='Muted.TLabel',wraplength=630).pack(anchor='w',pady=8)
        r=ttk.Frame(timeline); r.pack(fill='x',pady=6)
        ttk.Label(r,text='Start offset',width=24).pack(side='left'); duration(r,self.playback_offset,(-60,-30,-20,-15,-10,-5,0,5,10,15,20,30,60),signed=True)
        ttk.Label(timeline,text='Negative = earlier start / more elapsed; positive = later start / less elapsed.',style='Muted.TLabel',wraplength=630).pack(anchor='w')
        ttk.Checkbutton(timeline,text='Immediate end-of-track clearing (overrides inactivity below)',variable=self.clear_at_end).pack(anchor='w',pady=(12,0))
        r=ttk.Frame(timeline); r.pack(fill='x',pady=6)
        ttk.Label(r,text='End grace period',width=24).pack(side='left'); duration(r,self.end_grace,(0,10,20,30,60,120))
        ttk.Label(timeline,text='With progress enabled, completed tracks keep their original full progress bar until the selected clearing action. Missing timing data clears the activity rather than starting a counter. Phone pauses and seeking cannot be detected.',style='Muted.TLabel',wraplength=630).pack(anchor='w',pady=8)
        heading(timeline,'Inactivity after estimated end')
        r=ttk.Frame(timeline); r.pack(fill='x',pady=3)
        ttk.Combobox(r,textvariable=self.inactive_mode,values=('Keep last song','Hide timer','Mark inactive','Clear presence'),state='readonly',width=22).pack(side='left')
        r=ttk.Frame(timeline); r.pack(fill='x',pady=4)
        ttk.Label(r,text='Delay after estimated end',width=24).pack(side='left'); duration(r,self.inactive_delay,(0,15,30,60,300,900,3600))
        r=ttk.Frame(timeline); r.pack(fill='x',pady=3)
        ttk.Label(r,text='Inactive text',width=24).pack(side='left'); ttk.Entry(r,textvariable=self.inactive_label).pack(side='left',fill='x',expand=True,padx=8)
        ttk.Label(timeline,text='Uses estimated song end, not actual pause. Clear-at-end takes priority. With progress enabled, Hide timer clears the card instead (Discord may otherwise show its own timer). Mark inactive keeps the progress bar. New plays restore presence.',wraplength=650,style='Muted.TLabel').pack(anchor='w')
        buttons(timeline,[('Apply inactivity settings',self.apply_settings,True)])
        appearance=ttk.Frame(tabs,padding=12); tabs.add(appearance,text='Appearance')
        appearance_nav=ttk.Notebook(appearance); appearance_nav.pack(fill='both',expand=True)
        editor_page=ttk.Frame(appearance_nav,padding=6); basic=ttk.Frame(appearance_nav,padding=10)
        appearance_nav.add(editor_page,text='Template editor'); appearance_nav.add(basic,text='Basic layout')
        self.build_appearance(editor_page)
        basic=self.scroll_page(basic)
        heading(basic,'Basic layout (used when custom templates are disabled)')
        r=ttk.Frame(basic); r.pack(fill='x',pady=5); ttk.Label(r,text='Custom activity name',width=24).pack(side='left'); ttk.Entry(r,textvariable=self.activity_name).pack(side='left',fill='x',expand=True)
        for text,var in [('Use song title as activity name',self.title_as_name),('Include album when distinct',self.show_album),('Show cover artwork',self.show_art),('Include SoundCloud link button',self.show_button)]: ttk.Checkbutton(basic,text=text,variable=var).pack(anchor='w')
        buttons(basic,[('Save & apply',self.apply_settings,True)])
        tabs.add(self.custom_tab,text='Custom presence'); tabs.add(settings_host,text='Settings'); tabs.add(logs,text='Activity log')
        logopts=ttk.Frame(logs); logopts.pack(fill='x',pady=4)
        ttk.Checkbutton(logopts,text='Hide unchanged polls',variable=self.quiet).pack(side='left')
        ttk.Checkbutton(logopts,text='Persistent log',variable=self.persist_log).pack(side='left',padx=8)
        logopts=ttk.Frame(logs); logopts.pack(fill='x',pady=3)
        ttk.Label(logopts,text='Memory limit (100–3000)').pack(side='left'); ttk.Combobox(logopts,textvariable=self.log_limit,values=('100','500','1000','2000','3000'),width=7).pack(side='left')
        ttk.Button(logopts,text='Save',command=self.save_settings).pack(side='right')
        filterbar=ttk.Frame(logs); filterbar.pack(fill='x')
        for i,k in enumerate(self.log_categories):
            ttk.Checkbutton(filterbar,text=k,variable=self.log_filters[k],command=self.refresh_log).grid(row=i//3,column=i%3,sticky='w')
        ttk.Label(logs,text='Filters affect the on-screen log only. Uncheck Hide unchanged polls to capture every successful poll.',wraplength=650,style='Muted.TLabel').pack(anchor='w')
        r=ttk.Frame(logs); r.pack(fill='both',expand=True)
        self.logbox=tk.Text(r,background=panel,foreground=fg,insertbackground=fg,relief='flat',font=('Consolas',10),wrap='word',state='disabled',padx=10,pady=10)
        scroll=ttk.Scrollbar(r,command=self.logbox.yview); self.logbox.configure(yscrollcommand=scroll.set); scroll.pack(side='right',fill='y'); self.logbox.pack(side='left',fill='both',expand=True)
        buttons(logs,[('Copy log',self.copy_log,False),('Clear log',self.clear_log,False)])
        self.log('Ready. Paste your token and click Connect / Save in Accounts.')
    def duration_control(self,parent,var,presets,base=1,minimum=0,signed=False):
        units={'Seconds':1,'Minutes':60,'Hours':3600} if base==1 else {'Minutes':60,'Hours':3600}
        try: seconds=int(var.get())*base
        except ValueError: seconds=minimum*base
        unit=next((k for k,m in reversed(list(units.items())) if seconds and seconds%m==0),next(iter(units)))
        amount=tk.StringVar(value=str(seconds//units[unit]))
        unitvar=tk.StringVar(value=unit)
        box=ttk.Combobox(parent,textvariable=amount,width=8)
        box.pack(side='left',padx=(8,3))
        selector=ttk.Combobox(parent,textvariable=unitvar,values=tuple(units),state='readonly',width=9)
        selector.pack(side='left')
        def choices():
            m=units[unitvar.get()]
            box.configure(values=tuple(str(p*base//m) for p in presets if p*base%m==0))
        def commit(*args):
            try:
                n=int(amount.get())*units[unitvar.get()]
                if n%base or (not signed and n<minimum*base) or abs(n)>2147483647 or (var in (self.interval,self.idle_interval) and n>2147483): raise ValueError()
            except ValueError:
                box.configure(foreground='#ff7777'); return False
            box.configure(foreground='#edf0f4'); var.set(str(n//base)); return True
        def change_unit(*args):
            choices(); commit()
        choices()
        amount.trace_add('write',commit)
        selector.bind('<<ComboboxSelected>>',change_unit)
        if var is self.interval:
            def reschedule(*args):
                if commit() and self.running and not self.busy and time.monotonic()>=self.block_until:
                    self.schedule()
            box.bind('<<ComboboxSelected>>',reschedule)
            box.bind('<Return>',reschedule)
        if not hasattr(self,'duration_validators'): self.duration_validators=[]
        self.duration_validators.append(commit)

    def get_template(self):
        return {k:v.get() for k,v in self.template_fields.items()} if self.template_enabled.get() else None

    def scroll_page(self, host):
        canvas=tk.Canvas(host,background='#14171c',highlightthickness=0,width=1,height=1)
        scrollbar=ttk.Scrollbar(host,orient='vertical',command=canvas.yview)
        host.columnconfigure(0,weight=1); host.rowconfigure(0,weight=1)
        canvas.grid(row=0,column=0,sticky='nsew')
        content=ttk.Frame(canvas,padding=(0,0,5,0))
        item=canvas.create_window(0,0,window=content,anchor='nw')
        def sync(*args):
            canvas.itemconfigure(item,width=max(1,canvas.winfo_width()))
            height=content.winfo_reqheight()
            canvas.configure(scrollregion=(0,0,canvas.winfo_width(),height))
            if height>canvas.winfo_height(): scrollbar.grid(row=0,column=1,sticky='ns')
            else:
                scrollbar.grid_remove(); canvas.yview_moveto(0)
        canvas.configure(yscrollcommand=scrollbar.set)
        content.bind('<Configure>',sync); canvas.bind('<Configure>',sync)
        def wheel(event):
            if not canvas.winfo_ismapped() or not scrollbar.winfo_ismapped(): return
            steps=-int(event.delta/120) if getattr(event,'delta',0) else (-1 if event.num==4 else 1)
            canvas.yview_scroll(steps,'units'); return 'break'
        def bind_children(widget):
            if not isinstance(widget,(ttk.Combobox,tk.Text)):
                for sequence in ('<MouseWheel>','<Button-4>','<Button-5>'): widget.bind(sequence,wheel,add='+')
            if isinstance(widget,(ttk.Entry,ttk.Combobox,ttk.Button,ttk.Checkbutton)):
                def reveal(event):
                    if not canvas.winfo_ismapped(): return
                    y=widget.winfo_rooty()-content.winfo_rooty(); top=canvas.canvasy(0); h=canvas.winfo_height(); total=max(1,content.winfo_reqheight())
                    if y<top: canvas.yview_moveto(max(0,y-4)/total)
                    elif y+widget.winfo_height()>top+h: canvas.yview_moveto(max(0,y+widget.winfo_height()-h+4)/total)
                widget.bind('<FocusIn>',reveal,add='+')
            for child in widget.winfo_children(): bind_children(child)
        self.root.after_idle(lambda: (bind_children(host),sync()))
        return content

    def build_appearance(self,parent):
        top=ttk.Frame(parent); top.pack(fill='x',pady=(0,5))
        ttk.Checkbutton(top,text='Use custom music template',variable=self.template_enabled).pack(side='left')
        self.dirty_label=ttk.Label(top); self.dirty_label.pack(side='right')
        actions=ttk.Frame(parent); actions.pack(fill='x',pady=(0,5))
        for label,cmd in [('Apply template',self.apply_template),('Revert',self.revert_template),('Restore defaults',self.reset_template)]:
            ttk.Button(actions,text=label,command=cmd,style='Accent.TButton' if label=='Apply template' else 'TButton').pack(side='left',padx=(0,5))
        body=ttk.Frame(parent); body.pack(fill='both',expand=True)
        preview_host=ttk.Frame(body,width=300); preview_host.pack(side='right',fill='y',padx=(8,0)); preview_host.grid_propagate(False)
        preview=self.scroll_page(preview_host)
        ttk.Label(preview,text='Live Discord preview',font=('Segoe UI',11,'bold')).pack(anchor='w')
        self.preview_mode=tk.StringVar(value='Music')
        ttk.Combobox(preview,textvariable=self.preview_mode,values=('Music','Custom presence','Inactive music','Basic music'),state='readonly',width=21).pack(fill='x',pady=4)
        self.preview_canvas=tk.Canvas(preview,width=270,height=250,bg='#232428',highlightthickness=0); self.preview_canvas.pack(pady=4)
        ttk.Label(preview,text='Preview only. Apply to publish. Artwork is shown as a placeholder.',wraplength=270,style='Muted.TLabel').pack(anchor='w')
        host=ttk.Frame(body); host.pack(side='left',fill='both',expand=True)
        editor=self.scroll_page(host)
        self.preview_counts={}; self.template_entries={}; self.last_template_entry=None
        def heading(text): ttk.Label(editor,text=text,font=('Segoe UI',11,'bold')).pack(anchor='w',pady=(9,3))
        def field(k,label):
            r=ttk.Frame(editor); r.pack(fill='x',pady=(3,0))
            ttk.Label(r,text=label).pack(side='left')
            if k in ('name','details','state','large_text','small_text','button_label','button2_label'):
                count=ttk.Label(r); count.pack(side='right'); self.preview_counts[k]=(count,32 if k.endswith('_label') else 128)
            entry=ttk.Entry(editor,textvariable=self.template_fields[k]); entry.pack(fill='x',pady=(0,3)); self.template_entries[k]=entry
            entry.bind('<FocusIn>',lambda e,w=entry:setattr(self,'last_template_entry',w))
        heading('Text and status')
        for k,label in [('name','Activity name'),('details','First line'),('state','Second line')]: field(k,label)
        ttk.Label(editor,text='Status text').pack(anchor='w')
        ttk.Combobox(editor,textvariable=self.template_fields['status_display'],values=('Activity name','Details','State'),state='readonly').pack(fill='x')
        heading('Insert a variable')
        variable=tk.StringVar(value='%title%')
        r=ttk.Frame(editor); r.pack(fill='x')
        ttk.Combobox(r,textvariable=variable,values=('%title%','%artist%','%album%','%distinct_album%','%duration%','%artwork%','%url%','%app%'),state='readonly',width=16).pack(side='left',fill='x',expand=True)
        def insert():
            entry=self.last_template_entry or self.template_entries['name']
            entry.insert(entry.index('insert'),variable.get()); entry.focus_set()
        ttk.Button(r,text='Insert',command=insert).pack(side='right')
        ttk.Label(editor,text='Select a text field first. Optional groups such as [%album% • ] disappear if metadata is missing.',wraplength=250,style='Muted.TLabel').pack(anchor='w',pady=4)
        heading('Artwork and tooltips')
        for k,label in [('large_image','Cover URL / key'),('large_text','Cover tooltip'),('fallback','Fallback artwork'),('small_image','Small image URL / key'),('small_text','Small tooltip')]: field(k,label)
        for prefix,label in [('button','Link button 1'),('button2','Link button 2')]:
            heading(label)
            ttk.Combobox(editor,textvariable=self.template_fields[prefix+'_visible'],values=('Yes','No'),state='readonly',width=8).pack(anchor='w')
            field(prefix+'_label','Button text'); field(prefix+'_url','Button URL')
        heading('Saved layouts')
        self.music_preset_combo=ttk.Combobox(editor,textvariable=self.music_preset_name,values=sorted(self.music_presets)); self.music_preset_combo.pack(fill='x')
        for items in [[('Save',self.save_music_preset),('Load',self.load_music_preset),('Rename',self.rename_music_preset)],[('Duplicate',self.duplicate_music_preset),('Delete',self.delete_music_preset)],[('Import layout',self.import_layout),('Export layout',self.export_layout)]]:
            r=ttk.Frame(editor); r.pack(fill='x',pady=3)
            for label,cmd in items: ttk.Button(r,text=label,command=cmd).pack(side='left',fill='x',expand=True)
        self.tabs.bind('<<NotebookTabChanged>>',lambda e:self.queue_preview(),add='+')
        for var in list(self.template_fields.values())+list(self.custom_fields.values())+[self.template_enabled,self.preview_mode,self.custom_elapsed,self.show_progress,self.activity_name,self.title_as_name,self.show_album,self.show_art,self.show_button,self.track,self.inactive_label]:
            var.trace_add('write',lambda *args:self.queue_preview())
        self.draw_preview()

    def queue_preview(self):
        if not self.preview_pending:
            self.preview_pending=True
            self.root.after_idle(self.run_queued_preview)
    def run_queued_preview(self):
        self.preview_pending=False
        if not self.opt_background_preview.get() or self.tabs.tab(self.tabs.select(),'text')=='Appearance': self.draw_preview()
    def layout_data(self):
        return {'enabled':self.template_enabled.get(),'fields':{k:v.get() for k,v in self.template_fields.items()}}
    def set_layout(self,data):
        if not isinstance(data,dict) or not isinstance(data.get('fields'),dict): raise ValueError('Invalid layout')
        fields=data['fields']
        if any(not isinstance(v,str) or len(v)>4096 for k,v in fields.items() if k in self.template_fields): raise ValueError('Invalid template field')
        for k,v in self.template_fields.items(): v.set(fields.get(k,self.template_defaults[k]))
        self.template_enabled.set(bool(data.get('enabled',True)))
        self.draw_preview()
    def revert_template(self):
        self.set_layout({'enabled':self.applied_template is not None,'fields':self.saved_template})
    def refresh_music_presets(self):
        self.music_preset_combo.configure(values=sorted(self.music_presets)); self.save_settings()
    def save_music_preset(self):
        name=self.music_preset_name.get().strip()
        if not name: return
        if name in self.music_presets and not messagebox.askyesno('Layout','Overwrite this layout?',parent=self.root): return
        self.music_presets[name]=self.layout_data(); self.refresh_music_presets()
    def load_music_preset(self):
        data=self.music_presets.get(self.music_preset_name.get())
        if data:
            try: self.set_layout(data)
            except ValueError as e: messagebox.showerror('Layout',str(e),parent=self.root)
    def rename_music_preset(self): self.copy_music_preset(True)
    def duplicate_music_preset(self): self.copy_music_preset(False)
    def copy_music_preset(self,rename):
        old=self.music_preset_name.get()
        if old not in self.music_presets: return
        name=simpledialog.askstring('Layout','New name:',parent=self.root)
        if not name or not name.strip() or name.strip() in self.music_presets: return
        name=name.strip(); self.music_presets[name]=json.loads(json.dumps(self.music_presets[old]))
        if rename: del self.music_presets[old]
        self.music_preset_name.set(name); self.refresh_music_presets()
    def delete_music_preset(self):
        name=self.music_preset_name.get()
        if name in self.music_presets and messagebox.askyesno('Layout','Delete this layout?',parent=self.root):
            del self.music_presets[name]; self.music_preset_name.set(''); self.refresh_music_presets()
    def import_layout(self):
        path=filedialog.askopenfilename(parent=self.root,filetypes=[('JSON layout','*.json')])
        if path:
            try:
                if os.path.getsize(path)>100000: raise ValueError('Layout too large')
                self.set_layout(json.loads(Path(path).read_text(encoding='utf-8')))
            except (OSError,ValueError,TypeError) as e: messagebox.showerror('Layout',str(e),parent=self.root)
    def export_layout(self):
        path=filedialog.asksaveasfilename(parent=self.root,defaultextension='.json',filetypes=[('JSON layout','*.json')])
        if path:
            try: Path(path).write_text(json.dumps(self.layout_data(),indent=2),encoding='utf-8')
            except OSError as e: messagebox.showerror('Layout',str(e),parent=self.root)

    def apply_template(self):
        self.applied_template=self.get_template(); self.saved_template={k:v.get() for k,v in self.template_fields.items()}
        self.save_settings(); self.log('Settings: music template applied')
        if not self.custom_mode: self.rpc_publish()
        self.draw_preview()

    def reset_template(self):
        for k,v in self.template_defaults.items(): self.template_fields[k].set(v)
        self.draw_preview()

    def draw_preview(self):
        if not hasattr(self,'preview_canvas'): return
        c=self.preview_canvas; c.delete('all')
        now=int(time.time()*1000)
        sample=((0,now-63000),'Example song','Example artist','Example album','https://example.invalid/cover.png','https://soundcloud.com/',210000)
        current=self.current or sample
        mode=self.preview_mode.get()
        if mode=='Custom presence':
            f={k:v.get().strip() for k,v in self.custom_fields.items()}
            a={k:f[k] for k in ('name','details','state')}; a['type']={'Playing':0,'Listening':2,'Watching':3,'Competing':5}.get(f['type'],0)
            a['assets']={k:f[k] for k in ('large_image','small_image') if f[k]}
            if f['button_label'] and f['button_url']: a['buttons']=[{'label':f['button_label']}]
            if self.custom_elapsed.get(): a['timestamps']={'start':self.custom_started or now}
        else:
            ts=self.timeline_state()[2] if self.current else ({'start':now-63000,'end':now+147000} if self.show_progress.get() else None)
            a=music_activity(current,self.activity_name.get(),self.title_as_name.get(),self.show_album.get(),self.show_art.get(),self.show_button.get(),ts,None if mode=='Basic music' else {k:v.get() for k,v in self.template_fields.items()})
            if mode=='Inactive music':
                a['state']=self.inactive_label.get() or 'Listening inactive'
        dirty=self.layout_data()!={'enabled':self.applied_template is not None,'fields':self.saved_template}
        self.dirty_label.configure(text=('Unapplied changes' if dirty else 'Layout applied') if self.template_enabled.get() else 'Template disabled — preview only')
        key,title,artist,album,art,url,duration=current
        distinct=album if album and album.strip().casefold() not in (artist.strip().casefold(),title.strip().casefold()) else ''
        try: seconds=int(duration)//1000
        except (ValueError,TypeError): seconds=0
        values=dict(title=title,artist=artist,album=album,distinct_album=distinct,artwork=art or '',url=url or '',duration='%d:%02d'%(seconds//60,seconds%60),app=self.activity_name.get())
        for k,(widget,limit) in self.preview_counts.items():
            n=len(expand_template(self.template_fields[k].get(),values)); widget.configure(text='%d/%d'%(n,limit),foreground='#f87171' if n>limit else '#a0a0a0')
        fg='#f2f3f5'; muted='#b5bac1' 
        c.create_text(12,15,text={0:'PLAYING',2:'LISTENING TO',3:'WATCHING',5:'COMPETING IN'}.get(a.get('type'),'ACTIVITY'),anchor='nw',fill=muted,font=('Segoe UI',9,'bold'))
        assets=a.get('assets',{}); x=12
        if assets.get('large_image'):
            c.create_rectangle(12,42,76,106,fill='#41434a',outline=''); c.create_text(44,74,text='♫',fill=fg,font=('Segoe UI',25)); x=86
            if assets.get('small_image'): c.create_oval(59,89,82,112,fill='#5865f2',outline='#232428',width=3)
        elif assets.get('small_image'):
            c.create_oval(12,42,36,66,fill='#5865f2',outline=''); x=44
        y=42
        for k in ('name','details','state'):
            if a.get(k):
                text=a[k]; limit=25 if x>12 else 35
                c.create_text(x,y,text=text[:limit]+('…' if len(text)>limit else ''),anchor='nw',fill=fg if k=='name' else muted,font=('Segoe UI',10,'bold' if k=='name' else 'normal')); y+=21
        ts=a.get('timestamps',{}); y=max(y+8,116)
        if 'end' in ts and ts['end']>ts['start']:
            total=(ts['end']-ts['start'])//1000; elapsed=max(0,min(total,(now-ts['start'])//1000)); frac=elapsed/max(1,total)
            c.create_rectangle(12,y,258,y+4,fill='#4e5058',outline=''); c.create_rectangle(12,y,12+246*frac,y+4,fill=fg,outline='')
            fmt=lambda n:'%d:%02d'%(n//60,n%60)
            c.create_text(12,y+9,text=fmt(elapsed),anchor='nw',fill=muted,font=('Segoe UI',9)); c.create_text(258,y+9,text=fmt(total),anchor='ne',fill=muted,font=('Segoe UI',9)); y+=35
        elif 'start' in ts:
            c.create_text(12,y,text='Elapsed time',anchor='nw',fill='#23a55a'); y+=25
        for button in a.get('buttons',[]):
            c.create_rectangle(12,y,258,y+30,fill='#4e5058',outline=''); c.create_text(135,y+15,text=button['label'][:32],fill=fg,font=('Segoe UI',10,'bold')); y+=35

    def connect_account(self):
        if self.custom_mode:
            self.log("SoundCloud: account verification unavailable in custom mode; return to music first")
            return
        if self.busy:
            self.log('Wait for the current request to finish, then click Connect / Save.')
            return
        token=self.normalized_token()
        if not token or any(c.isspace() for c in token) or (token_expiry(token) or 0)<=time.time():
            self.log('SoundCloud: token verification not started; invalid or expired token')
            messagebox.showwarning('SoundCloud','Paste a current, unexpired access token. Use Help: Get token for instructions.',parent=self.root)
            return
        self.token.set(token)
        self.pending_save=token
        self.rejected_token=None
        if self.restricted:
            if not messagebox.askyesno('SoundCloud access restriction','Only retry after confirming SoundCloud access is restored in your normal browser. Try one verification request?',parent=self.root):
                self.pending_save=None; return
            self.restricted=False; self.save_usage()
        if self.resume_on_auth:
            self.running=True
            self.rpc_enabled=True
        self.log('Verifying access token with SoundCloud')
        self.fetch(manual=True)

    def show_connection_guide(self):
        win=tk.Toplevel(self.root); win.title('Help - retrieve SoundCloud token')
        win.geometry('660x500'); win.minsize(550,440); win.configure(bg='#14171c')
        frame=ttk.Frame(win,padding=20); frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Get your SoundCloud access token',font=('Segoe UI',16,'bold')).pack(anchor='w',pady=(0,12))
        steps=[
            'Open soundcloud.com in your normal Firefox browser. Sign in only if needed.',
            'Press Shift + F9 to open Storage Inspector.',
            'Expand Cookies on the left, then select https://soundcloud.com.',
            'Find oauth_token. Do NOT copy oauth_refresh_token.',
            'Double-click the Value cell, press Ctrl+A and Ctrl+C. Press Esc to avoid changing the cookie.',
            'Return to the Accounts tab, paste with Ctrl+V, then click Connect / Save.'
        ]
        for i,step in enumerate(steps,1):
            ttk.Label(frame,text=str(i)+'.  '+step,wraplength=580,justify='left').pack(anchor='w',pady=6)
        ttk.Label(frame,text='If the token is expired, refresh SoundCloud while signed in and copy the current value. Keep tokens private. Renewal is manual.',style='Muted.TLabel',wraplength=580).pack(anchor='w',pady=8)
        row=ttk.Frame(frame); row.pack(fill='x',pady=8)
        ttk.Button(row,text='Open SoundCloud',command=lambda:webbrowser.open('https://soundcloud.com/you/history'),style='Accent.TButton').pack(side='left')
        ttk.Button(row,text='Close help',command=win.destroy).pack(side='right')

    def normalized_token(self):
        token=self.token.get().strip()
        if token.lower().startswith('oauth '): token=token[6:].strip()
        return token

    def remember_token(self, token=None):
        token=token or self.normalized_token(); expiry=token_expiry(token)
        if not token or any(c.isspace() for c in token) or not expiry or expiry<=time.time():
            messagebox.showerror('Account','Enter a valid, unexpired access token.',parent=self.root); return
        try:
            encrypted=protect(token.encode('utf-8'))
            self.token_path.parent.mkdir(parents=True,exist_ok=True)
            temp=self.token_path.with_suffix('.tmp'); temp.write_bytes(encrypted); os.replace(str(temp),str(self.token_path))
            self.log('Access token encrypted and saved')
        except OSError:
            messagebox.showerror('Account','Could not securely save the token. It remains in memory only.',parent=self.root)

    def restore_token(self):
        try:
            token=protect(self.token_path.read_bytes(),True).decode('utf-8')
            if (token_expiry(token) or 0)>time.time():
                self.token.set(token); self.log('Saved access token restored')
            else:
                self.token.set(token); self.log('Saved access token expired; reconnect in Accounts')
        except FileNotFoundError: pass
        except (OSError,UnicodeError): self.log('Could not restore encrypted token; reconnect in Accounts')

    def forget_account(self):
        if self.custom_mode:
            self.log("SoundCloud: return to music before forgetting account"); return
        self.generation+=1; self.pending_save=None; self.resume_on_auth=False; self.pause(); self.token.set(''); self.current=None; self.last=None
        self.track.set('No track retrieved'); self.artist.set(''); self.album.set(''); self.art.set(''); self.rpc_clear()
        try:
            self.token_path.unlink()
        except FileNotFoundError: pass
        except OSError:
            messagebox.showerror('Account','Could not delete saved credential.',parent=self.root); return
        self.log('Local account forgotten (SoundCloud browser session unchanged)')

    def auth_tick(self):
        token=self.normalized_token(); expiry=token_expiry(token)
        if not token: self.auth_status.set('No token')
        elif not expiry: self.auth_status.set('Expiration unknown — access token required')
        elif token==self.rejected_token: self.auth_status.set('Access denied — get a new token or retry later')
        else:
            remaining=int(expiry-time.time())
            if remaining<=0:
                self.auth_status.set('Expired — paste a new access token and click Connect / Save')
                if self.running and not self.custom_mode:
                    self.log('SoundCloud: access token expired; polling suspended')
                    self.pause(); self.resume_on_auth=True; self.status.set('Token expired; reconnect SoundCloud'); self.rpc_clear()
            else:
                local_expiry=datetime.datetime.fromtimestamp(expiry).strftime('%Y-%m-%d %H:%M:%S')
                utc_expiry=datetime.datetime.utcfromtimestamp(expiry).strftime('%Y-%m-%d %H:%M:%S UTC')
                self.auth_status.set('Remaining: %dh %02dm %02ds | Expires: %s (local)\n%s | Based on token exp and PC clock'%(remaining//3600,remaining//60%60,remaining%60,local_expiry,utc_expiry))
        self.root.after(1000,self.auth_tick)

    def load_usage(self):
        try:
            obj=json.loads(self.block_path.read_text(encoding='utf-8'))
            now=time.time()
            self.request_times=deque(sorted(float(x) for x in obj.get('requests',[]) if now-86400<float(x)<=now))
            self.block_until=time.monotonic()+max(0,float(obj.get('cooldown_until',0))-now)
            self.restricted=bool(obj.get('restricted',False))
        except (OSError,ValueError,TypeError,OverflowError): pass

    def save_usage(self):
        try:
            self.block_path.parent.mkdir(parents=True,exist_ok=True)
            temp=self.block_path.with_suffix('.tmp')
            temp.write_text(json.dumps({'requests':list(self.request_times),'cooldown_until':time.time()+max(0,self.block_until-time.monotonic()),'restricted':self.restricted}),encoding='utf-8')
            os.replace(str(temp),str(self.block_path))
        except OSError: self.log('SoundCloud: unable to persist request usage; session counters remain active')

    def prune_usage(self, now=None):
        now=time.time() if now is None else now
        while self.request_times and self.request_times[0]<=now-86400: self.request_times.popleft()

    def budget_delay(self):
        self.prune_usage()
        try: budget=int(self.daily_budget.get())
        except ValueError: budget=8640
        if budget<0: budget=8640
        if budget and len(self.request_times)>=budget:
            return max(1,self.request_times[len(self.request_times)-budget]+86400-time.time()+1)
        return 0

    @staticmethod
    def read_limit_headers(headers):
        if not headers: return {}
        names=('X-RateLimit-Limit','X-RateLimit-Remaining','X-RateLimit-Reset','RateLimit-Limit','RateLimit-Remaining','RateLimit-Reset','Retry-After')
        return {name:str(headers.get(name))[:120] for name in names if headers.get(name) is not None}

    @staticmethod
    def reset_delay(headers):
        delays=[]
        for key in ('Retry-After','X-RateLimit-Reset','RateLimit-Reset'):
            val=headers.get(key)
            if not val: continue
            try:
                n=float(val)
                if not math.isfinite(n): continue
                if key=='X-RateLimit-Reset':
                    if n>1e12: n/=1000
                    if n>1e9: n-=time.time()
                    else: continue  # undocumented ambiguous reset unit
                elif key=='RateLimit-Reset' and n>1e9: n-=time.time()
                delays.append(max(0,n))
            except ValueError:
                try: delays.append(max(0,parsedate_to_datetime(val).timestamp()-time.time()))
                except (ValueError,TypeError,OverflowError): pass
        return max(delays or [0])

    def poll_seconds(self):
        normal=max(1,int(self.interval.get()))
        try:
            if self.adaptive.get() and time.monotonic()-self.last_change>=int(self.idle_minutes.get())*60:
                return max(normal,int(self.idle_interval.get()))
        except ValueError: pass
        return normal

    def save_budget(self):
        try:
            if int(self.daily_budget.get())<0 or int(self.inactive_delay.get())<0: raise ValueError()
        except ValueError:
            messagebox.showwarning('Request budget','Enter nonnegative whole numbers for budget and inactivity delay.',parent=self.root); return
        self.save_settings()
        self.log('SoundCloud: request budget saved')
        if self.running and not self.busy: self.schedule(max(self.poll_seconds(),self.block_until-time.monotonic(),self.budget_delay()))

    def apply_settings(self):
        if not all(validate() for validate in self.duration_validators):
            messagebox.showwarning('Invalid duration','Enter valid whole numbers in the highlighted duration fields.',parent=self.root); return
        try:
            if int(self.daily_budget.get())<0 or int(self.inactive_delay.get())<0: raise ValueError()
        except ValueError:
            messagebox.showwarning('Request budget','Enter nonnegative whole numbers for budget and inactivity delay.',parent=self.root); return
        self.save_settings();
        if not self.custom_mode: self.rpc_publish()
        self.log('Settings applied')
        if self.running and not self.busy: self.schedule(max(self.poll_seconds(),self.block_until-time.monotonic()))
    def callback_error(self,kind,error,tb):
        self.log("Application: "+kind.__name__+": "+self.safe_error(error,self.normalized_token()))
    def copy_log(self):
        self.root.clipboard_clear(); self.root.clipboard_append(self.logbox.get('1.0','end-1c'))
    def clear_log(self):
        self.log_records.clear(); self.refresh_log()
    def log(self, msg):
        msg=self.safe_error(msg,self.normalized_token())
        try:
            if not self.persist_log.get(): raise OSError('logging disabled')
            path=self.config_path.with_name('activity.log'); path.parent.mkdir(parents=True,exist_ok=True)
            if path.exists() and path.stat().st_size>1000000:
                os.replace(str(path),str(path.with_suffix('.old.log')))
            with path.open('a',encoding='utf-8') as f: f.write(datetime.datetime.now().isoformat(timespec='seconds')+'  '+msg+'\n')
        except OSError: pass
        category=self.log_category(msg)
        line=datetime.datetime.now().strftime('%H:%M:%S')+'  '+msg+'\n'
        try: limit=max(100,min(3000,int(self.log_limit.get())))
        except ValueError: limit=1000
        self.log_records.append((category,line))
        while len(self.log_records)>limit: self.log_records.popleft()
        if self.log_filters[category].get():
            self.logbox.configure(state='normal'); self.logbox.insert('end',line)
            self.logbox.see('end'); self.logbox.configure(state='disabled')
        if int(self.logbox.index('end-1c').split('.')[0])>limit+1:
            self.logbox.configure(state='normal'); self.logbox.delete('1.0','%d.0'%(int(self.logbox.index('end-1c').split('.')[0])-limit)); self.logbox.configure(state='disabled')

    @staticmethod
    def log_category(msg):
        m=msg.lower()
        if re.search(r'http [45]\d\d|error|failed|rejected|denied|restricted|rate limited|exception|could not|disconnected',m): return 'Errors'
        if m.startswith('http 200: new'): return 'New tracks'
        if m.startswith('http 200: unchanged'): return 'Unchanged polls'
        if m.startswith('discord:'): return 'Discord'
        if m.startswith('account:') or 'token' in m or 'authorization' in m: return 'Account'
        return 'Other'

    def refresh_log(self):
        self.logbox.configure(state='normal'); self.logbox.delete('1.0','end')
        self.logbox.insert('end',''.join(line for k,line in self.log_records if self.log_filters[k].get()))
        self.logbox.see('end'); self.logbox.configure(state='disabled')
        self.save_settings()

    def cancel(self):
        self.next_poll_at=None
        if self.timer is not None:
            self.root.after_cancel(self.timer); self.timer=None
    def pause(self):
        self.manual_queued=False; self.resume_on_auth=False; self.running=False; self.cancel(); self.status.set('Paused (in-flight request may finish)'); self.log('Polling paused')
    def start(self):
        if self.custom_mode:
            self.log("SoundCloud: switch back to music before starting polling"); return
        if self.running:
            self.log("SoundCloud: polling already running"); return
        self.running=True; self.resume_on_auth=False; self.rpc_enabled=True; self.fetch()
    def schedule(self, seconds=None):
        self.cancel()
        if self.running:
            delay=max(0,seconds if seconds is not None else self.poll_seconds())
            self.next_poll_at=time.monotonic()+delay
            self.timer=self.root.after(int(1000*delay), self.fetch)
    def manual_refresh(self):
        self.log('SoundCloud: manual refresh requested')
        self.fetch(manual=True)

    def fetch(self, manual=False):
        self.cancel()
        if self.custom_mode:
            self.log('SoundCloud: refresh blocked while custom presence is active'); return
        if self.busy:
            if manual:
                self.manual_queued=True
                self.status.set('Request in progress — refresh queued')
                self.log('SoundCloud: request already running; manual refresh queued')
            else: self.log('SoundCloud: request already running')
            return
        if self.restricted:
            self.running=False; self.status.set('Access restricted — verify in Accounts'); self.log('SoundCloud: blocked after HTTP 403; use Connect / Save only after access is restored'); return
        budget_wait=self.budget_delay()
        if budget_wait>0:
            self.status.set('Daily request budget reached'); self.log('SoundCloud: rolling 24-hour request budget reached; next slot in %d seconds'%math.ceil(budget_wait)); self.schedule(budget_wait); return
        remaining=self.block_until-time.monotonic()
        if remaining>0:
            self.status.set('Backoff: retry in %d seconds' % (remaining+1)); self.log('SoundCloud: request blocked by cooldown; retry in %d seconds' % (remaining+1)); self.schedule(remaining+1); return
        token=self.token.get().strip()
        if token.lower().startswith('oauth '): token=token[6:].strip()
        if not token or any(c.isspace() for c in token):
            self.running=False; self.status.set('Enter a valid OAuth token'); self.log('SoundCloud: missing or malformed token; request not sent'); return
        if (token_expiry(token) or float('inf'))<=time.time():
            self.resume_on_auth=self.running; self.running=False; self.status.set('Token expired; reconnect SoundCloud'); self.log('SoundCloud: token expired; request not sent'); self.rpc_clear(); return
        if self.request_times and time.time()-self.request_times[-1]<5:
            wait=max(0.1,5-(time.time()-self.request_times[-1]))
            if manual: self.log('SoundCloud: minimum request spacing; retry in %.1fs'%wait)
            if self.running: self.schedule(wait)
            elif manual: self.status.set('Please retry manual refresh in %.1fs'%wait)
            return
        self.request_times.append(time.time()); self.save_usage()
        self.request_started=time.monotonic()
        self.request_manual=manual
        self.request_token=token
        self.busy=True; self.status.set('Requesting latest track...')
        if manual or not self.quiet.get(): self.log('SoundCloud: GET latest track ('+('manual' if manual else 'automatic')+')')
        threading.Thread(target=self.worker,args=(token,self.generation),daemon=True).start()
    def worker(self, token, generation):
        try:
            req=urllib.request.Request(URL,headers={'Authorization':'OAuth '+token,'Accept':'application/json','Cache-Control':'no-cache'})
            with urllib.request.urlopen(req,timeout=15) as res:
                headers=self.read_limit_headers(res.headers)
                data=json.load(res)
            self.results.put((generation,'ok',(data,time.monotonic(),headers)))
        except urllib.error.HTTPError as e:
            self.results.put((generation,'http',(e.code,e.headers.get('Retry-After'),str(e.reason),self.read_limit_headers(e.headers))))
        except Exception as e:
            self.results.put((generation,'error',type(e).__name__+': '+self.safe_error(e,token)))
    @staticmethod
    def safe_error(error,token):
        msg=str(error)
        if token: msg=msg.replace(token,'[redacted]')
        return re.sub(r'eyJ[A-Za-z0-9_.-]+','[redacted]',msg)[:350]

    def consume(self):
        try:
            while True:
                generation,kind,data=self.results.get_nowait(); self.busy=False
                if kind in ('ok','http'):
                    self.limit_headers=data[-1]
                    if kind=='http' and data[0]==403:
                        self.restricted=True; self.save_usage()
                    if kind=='http' and data[0]==429:
                        self.block_until=max(self.block_until,time.monotonic()+max(120,self.reset_delay(self.limit_headers))); self.save_usage()
                    if kind=='ok' and any(self.limit_headers.get(k)=='0' for k in ('X-RateLimit-Remaining','RateLimit-Remaining')):
                        self.block_until=max(self.block_until,time.monotonic()+max(60,self.reset_delay(self.limit_headers))); self.save_usage()
                        self.log('SoundCloud: reported quota exhausted; cooldown applied')
                if generation!=self.generation or self.custom_mode:
                    self.log('SoundCloud: completed request discarded after mode/account change')
                    if self.manual_queued and not self.custom_mode:
                        self.manual_queued=False; self.fetch(manual=True)
                    elif self.running: self.schedule()
                    continue
                self.last_latency=time.monotonic()-self.request_started
                if kind!='ok': self.total_errors+=1
                if kind=='ok':
                    data,received_at,_headers=data
                    self.last_latency=received_at-self.request_started
                    self.last_success=time.time()
                    if self.pending_save==self.request_token:
                        if self.remember.get(): self.remember_token(self.request_token)
                        else:
                            try: self.token_path.unlink()
                            except FileNotFoundError: pass
                            except OSError: self.log('Could not remove old saved token')
                        self.pending_save=None
                        self.resume_on_auth=False
                        self.log('SoundCloud authorization verified')
                        if self.rpc_enabled and self.requested_presence==('clear',): self.last=None
                    self.rejected_token=None
                    self.failures=0
                    try:
                        items=data['collection']
                        if not isinstance(items,list): raise TypeError('collection is not a list')
                        if not items:
                            self.current=None
                            if not self.empty_seen and self.rpc_connected and self.rpc_enabled: self.enqueue_presence('clear',None,('empty',))
                            self.empty_seen=True; self.track.set('No listening history'); self.artist.set(''); self.album.set(''); self.art.set(''); self.last=None; self.log('HTTP 200: empty history')
                        else:
                            self.empty_seen=False
                            item=items[0]; t=item['track']; key=(item['track_id'],item['played_at'])
                            title=t.get('title') or 'Unknown title'; artist=(t.get('publisher_metadata') or {}).get('artist') or (t.get('user') or {}).get('username') or 'Unknown artist'
                            album=(t.get('publisher_metadata') or {}).get('album_title') or ''
                            for var,value in ((self.track,title),(self.artist,artist),(self.album,album),(self.art,t.get('artwork_url') or '')):
                                if not self.opt_gui.get() or var.get()!=value: var.set(value)
                            self.current=(key,title,artist,album,t.get('artwork_url'),t.get('permalink_url'),t.get('full_duration') or t.get('duration'))
                            if key!=self.last:
                                self.last_change=time.monotonic()
                                self.detected_at=received_at
                                self.log('HTTP 200: NEW '+title+' — '+artist+' | played_at='+str(key[1])+' | HTTP %.0f ms'%(self.last_latency*1000)); self.last=key
                                self.rpc_update()
                            elif self.request_manual or not self.quiet.get(): self.log('HTTP 200: unchanged — '+title+' — '+artist)

                        self.status.set('Connected - '+('polling every %ds'%self.poll_seconds() if self.running else 'paused'))
                        self.schedule(max(0,self.poll_seconds()-(time.monotonic()-self.request_started),self.block_until-time.monotonic(),self.budget_delay()))
                    except (KeyError,TypeError,AttributeError,IndexError,ValueError) as e:
                        self.total_errors+=1; self.running=False; self.status.set('Unexpected response format'); self.log('SoundCloud: unexpected JSON structure; polling stopped: '+self.safe_error(e,self.request_token))
                elif kind=='http':
                    code,retry,reason,_headers=data; self.log('SoundCloud: HTTP %s %s'%(code,self.safe_error(reason,self.request_token)))
                    if code in (401,403):
                        self.rejected_token=self.request_token
                        self.pending_save=None
                        self.resume_on_auth=self.running or self.resume_on_auth
                        self.running=False; self.status.set('Access denied or token expired; reconnect in Accounts'); self.log('SoundCloud: authentication rejected; polling stopped'); self.rpc_clear()
                    elif code==429:
                        self.failures+=1
                        delay=max(min(3600,120*2**min(self.failures-1,5)),self.reset_delay(self.limit_headers))
                        if retry:
                            try: delay=max(delay,int(retry))
                            except ValueError:
                                try:
                                    from email.utils import parsedate_to_datetime
                                    delay=max(delay,parsedate_to_datetime(retry).timestamp()-time.time())
                                except (ValueError,TypeError,OverflowError): pass
                        self.block_until=max(self.block_until,time.monotonic()+delay); self.save_usage(); self.status.set('Rate limited; backoff %d seconds'%delay); self.log('SoundCloud: rate limited; cooldown %d seconds'%delay); self.schedule(delay)
                    elif code>=500:
                        self.retry('Server error')
                    else:
                        self.running=False; self.status.set('HTTP %d; polling stopped'%code)
                else: self.retry('SoundCloud: request failed: '+data)
                if self.manual_queued:
                    self.manual_queued=False
                    if kind=='ok': self.fetch(manual=True)
                    else: self.log('SoundCloud: queued refresh cancelled after request error; see above')
        except queue.Empty: pass
        self.consume_rpc()
        self.root.after(40,self.consume)
    def enable_custom(self):
        if self.custom_mode:
            self.tabs.select(self.custom_tab); return
        if not messagebox.askyesno('Switch to custom presence','Stop SoundCloud polling and replace your music presence with a custom activity? You can return to music at any time.',parent=self.root): return
        self.music_was_running=self.running or self.resume_on_auth
        self.pause(); self.generation+=1; self.pending_save=None
        self.rpc_clear(); self.custom_mode=True; self.custom_activity=None; self.custom_started=None
        self.mode_label.set('Custom mode active — music suspended')
        self.status.set('Custom mode — SoundCloud paused')
        self.tabs.tab(self.custom_tab,state='normal'); self.tabs.select(self.custom_tab)
        self.log('Mode: custom presence enabled; SoundCloud polling suspended')

    def disable_custom(self):
        if not self.custom_mode:
            self.tabs.select(self.home_tab); return
        if not messagebox.askyesno('Return to music','Clear the custom activity and return to SoundCloud music mode?',parent=self.root): return
        self.rpc_clear(); self.custom_mode=False; self.custom_activity=None
        self.tabs.select(self.home_tab)
        self.mode_label.set('Music mode active'); self.status.set('Music mode — paused')
        self.current=None; self.last=None; self.rpc_enabled=True
        self.log('Mode: music restored; refreshing SoundCloud')
        self.running=self.music_was_running; self.music_was_running=False
        if self.busy:
            self.manual_queued=True
            self.log('SoundCloud: waiting for previous request to finish')
        else: self.fetch(manual=True)

    def publish_custom(self):
        if not self.custom_mode:
            self.enable_custom()
            if not self.custom_mode: return
        f={k:v.get().strip() for k,v in self.custom_fields.items()}
        if not f['name']:
            messagebox.showwarning('Custom presence','Enter an activity name.',parent=self.root); return
        for k in ('large_image','small_image'):
            v=f[k]
            if v and (('://' in v and (not v.startswith('https://') or not urlsplit(v).netloc)) or ('://' not in v and not re.fullmatch(r'[A-Za-z0-9_-]+',v))):
                messagebox.showwarning('Custom presence','Use a public HTTPS image URL or a Discord asset key, not a local file path.',parent=self.root); return
        if bool(f['button_label'])!=bool(f['button_url']) or (f['button_url'] and (not f['button_url'].startswith(('https://','http://')) or not urlsplit(f['button_url']).netloc)):
            messagebox.showwarning('Custom presence','Provide both a button label and a valid HTTP(S) URL, or leave both blank.',parent=self.root); return
        activity={'name':f['name'][:128],'type':{'Playing':0,'Listening':2,'Watching':3,'Competing':5}.get(f['type'],0),'status_display_type':0,'instance':False}
        for k in ('details','state'):
            if f[k]: activity[k]=f[k][:128]
        assets={}
        for size in ('large','small'):
            if f[size+'_image']:
                assets[size+'_image']=f[size+'_image']
                if f[size+'_text']: assets[size+'_text']=f[size+'_text'][:128]
        if assets: activity['assets']=assets
        if f['button_label']: activity['buttons']=[{'label':f['button_label'][:32],'url':f['button_url']}]
        if self.custom_elapsed.get():
            if self.custom_started is None: self.custom_started=int(time.time()*1000)
            activity['timestamps']={'start':self.custom_started}
        else: self.custom_started=None
        self.custom_activity=activity; self.rpc_enabled=True; self.save_settings()
        if self.rpc_connected: self.enqueue_presence('custom',activity,('custom',json.dumps(activity,sort_keys=True)))
        else: self.log('Discord: custom activity ready; Connect / Switch to publish')

    def rpc_connect(self, automatic=False):
        app = self.app_id.get().strip()
        if not app.isdigit() or not 17 <= len(app) <= 20:
            self.log('Discord: enter a valid Application ID in Accounts'); return
        if self.rpc_busy: return
        self.app_id.set(app)
        self.save_settings()
        self.rpc_wanted=True
        self.rpc_busy=True; self.rpc_connected=False; self.published=None; self.requested_presence=None
        if not automatic: self.rpc_enabled=True
        self.rpc_status.set('Connecting...')
        self.rpc_commands.put(('connect',app))
    def rpc_disconnect(self):
        if self.rpc_busy: return
        self.rpc_wanted=False
        self.rpc_connected=False; self.rpc_busy=True; self.published=None; self.requested_presence=None
        self.rpc_commands.put(('disconnect',None))
    def rpc_clear(self):
        self.rpc_enabled=False; self.published=None; self.requested_presence=None
        if self.rpc_connected: self.enqueue_presence('clear',None,('clear',))
    def rpc_publish(self):
        if self.custom_mode: self.publish_custom(); return
        self.rpc_enabled=True; self.requested_presence=None; self.rpc_update()
    def timeline_state(self):
        if not self.current: return None,False,None
        try: offset=int(self.playback_offset.get())
        except ValueError: offset=0
        try: grace=max(0,int(self.end_grace.get()))
        except ValueError: grace=30
        bounds=playback_bounds(self.current[0][1],self.current[6],offset)
        clear,timestamps=playback_state(bounds,int(time.time()*1000),self.show_progress.get(),self.clear_at_end.get(),grace)
        if self.is_inactive(bounds):
            mode=self.inactive_mode.get()
            # Hiding timestamps can make Discord display an elapsed counter.
            # With progress enabled, clear instead of publishing an untimed card.
            if mode=='Clear presence' or (mode=='Hide timer' and self.show_progress.get()):
                clear=True; timestamps=None
        return bounds,clear,timestamps

    def is_inactive(self,bounds):
        try: delay=max(0,int(self.inactive_delay.get()))
        except ValueError: delay=30
        return bool(bounds and int(time.time()*1000)>=bounds[1]+delay*1000)

    def playback_tick(self):
        bounds,clear,_=self.timeline_state()
        if bounds:
            start,end=bounds; now=int(time.time()*1000)
            duration=(end-start)//1000; elapsed=max(0,min(duration,(now-start)//1000))
            fmt=lambda seconds: '%d:%02d'%(seconds//60,seconds%60)
            self.playback_label.set('Estimated: '+fmt(elapsed)+' / '+fmt(duration)+(' (ended)' if now>=end else ''))
        else: self.playback_label.set('Estimated playback unavailable')
        if clear and self.rpc_connected and self.rpc_enabled and not self.custom_mode and self.current:
            signature=(self.current[0],'ended')
            if self.requested_presence!=signature: self.enqueue_presence('clear',None,signature)
        elif self.is_inactive(bounds) and self.inactive_mode.get() in ('Hide timer','Mark inactive'):
            marker=(self.current,self.inactive_mode.get(),self.inactive_label.get(),self.requested_presence,self.rpc_connected,self.rpc_enabled,self.custom_mode)
            if not self.opt_idle_rpc.get() or marker!=self.last_inactive_signature:
                self.rpc_update(); self.last_inactive_signature=(self.current,self.inactive_mode.get(),self.inactive_label.get(),self.requested_presence,self.rpc_connected,self.rpc_enabled,self.custom_mode)
        else: self.last_inactive_signature=None
        if hasattr(self,'preview_canvas') and self.tabs.tab(self.tabs.select(),'text')=='Appearance': self.draw_preview()
        self.root.after(1000,self.playback_tick)

    def enqueue_presence(self, action, data, signature):
        self.rpc_sequence+=1
        self.requested_presence=signature
        queued=time.monotonic()
        detected=self.detected_at if action=='update' and self.detected_at is not None and queued-self.detected_at<10 else None
        self.rpc_commands.put((action,(self.rpc_sequence,signature,data,queued,detected)))

    def rpc_update(self):
        if self.custom_mode or not (self.rpc_connected and self.rpc_enabled and self.current): return
        _,clear,timestamps=self.timeline_state()
        if clear:
            signature=(self.current[0],'ended')
            if self.requested_presence!=signature:
                self.enqueue_presence('clear',None,signature)
            return
        bounds,_,_=self.timeline_state()
        inactive=self.is_inactive(bounds)
        activity=music_activity(self.current,self.activity_name.get().strip(),self.title_as_name.get(),self.show_album.get(),self.show_art.get(),self.show_button.get(),timestamps,self.applied_template)
        if inactive and self.inactive_mode.get()=='Mark inactive': activity['state']=(self.inactive_label.get().strip() or 'Listening inactive')[:128]
        if inactive and self.inactive_mode.get()=='Hide timer': activity.pop('timestamps',None)
        signature=(self.current[0],json.dumps(activity,sort_keys=True))
        if signature==self.requested_presence: return
        self.enqueue_presence('update',(activity,self.current[1],self.current[2]),signature)

    def rpc_worker(self):
        loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        rpc=None
        while True:
            action,data=self.rpc_commands.get()
            try:
                if action in ('connect','disconnect','quit'):
                    if rpc is not None:
                        try: rpc.clear()
                        except Exception: pass
                        try: rpc.close()
                        except Exception: pass
                        rpc=None
                    if action=='quit': break
                    if action=='disconnect':
                        self.rpc_events.put(('disconnected','Disconnected')); continue
                    from pypresence import Presence
                    if loop.is_closed():
                        loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
                    rpc=Presence(data, loop=loop)
                    rpc.connect()
                    self.rpc_events.put(('connected','Connected'))
                elif action in LatestCommands.activity and rpc is None:
                    continue
                elif action=='clear' and rpc is not None:
                    seq,sig,data,queued,detected=data; sent=time.monotonic()
                    rpc.clear(); self.rpc_events.put(('ack',(seq,sig,'Presence cleared',queued,sent,time.monotonic(),detected)))
                elif action=='custom' and rpc is not None:
                    seq,sig,data,queued,detected=data; sent=time.monotonic()
                    rpc.update(payload_override={'cmd':'SET_ACTIVITY','args':{'pid':os.getpid(),'activity':data},'nonce':str(uuid.uuid4())})
                    self.rpc_events.put(('ack',(seq,sig,'Custom published: '+data['name'],queued,sent,time.monotonic(),detected)))
                elif action=='update' and rpc is not None:
                    seq,sig,data,queued,detected=data
                    activity,title,artist=data
                    payload={'cmd':'SET_ACTIVITY','args':{'pid':os.getpid(),'activity':activity},'nonce':str(uuid.uuid4())}
                    sent=time.monotonic()
                    ts=activity.get('timestamps')
                    timing=('progress start=%s end=%s'%(ts['start'],ts['end'])) if ts and 'start' in ts and 'end' in ts else 'no timestamps (progress disabled)'
                    self.rpc_events.put(('info','Sending: '+title+' — '+artist+' | '+timing+' | queue %.0f ms'%((sent-queued)*1000)))
                    rpc.update(payload_override=payload)
                    self.rpc_events.put(('ack',(seq,sig,'Published: '+title+' — '+artist,queued,sent,time.monotonic(),detected)))
            except Exception as e:
                if rpc is not None:
                    try: rpc.close()
                    except Exception: pass
                    rpc=None
                self.rpc_events.put(('error',type(e).__name__+': '+str(e)[:200]))
        if not loop.is_closed(): loop.close()
    def consume_rpc(self):
        try:
            while True:
                kind,msg=self.rpc_events.get_nowait()
                if kind=='ack':
                    seq,sig,label,queued,sent,ack,detected=msg
                    self.published=sig
                    self.rpc_last_ack=ack-sent
                    self.log('Discord: '+label+' | IPC %.0f ms | queue %.0f ms'%((ack-sent)*1000,(sent-queued)*1000)+(' | detection to ACK %.0f ms'%((ack-detected)*1000) if detected is not None else ''))
                    continue
                self.log('Discord: '+msg)
                if kind=='connected':
                    self.rpc_busy=False; self.rpc_connected=True; self.requested_presence=None; self.rpc_status.set('Discord connected')
                    if self.custom_mode and self.custom_activity and self.rpc_enabled: self.enqueue_presence('custom',self.custom_activity,('custom',json.dumps(self.custom_activity,sort_keys=True)))
                    else: self.rpc_update()
                elif kind in ('error','disconnected'):
                    self.rpc_retry_at=time.monotonic()+30
                    self.rpc_busy=False; self.rpc_connected=False; self.published=None; self.requested_presence=None; self.rpc_status.set('Discord disconnected')
        except queue.Empty: pass
    def connect_on_open(self):
        self.rpc_connect()
        if (token_expiry(self.normalized_token()) or 0)>time.time(): self.start()
        else: self.log('Startup: SoundCloud token missing or expired; connect manually')
    def maintenance(self):
        if self.closing: return
        now=time.monotonic()
        if self.auto_reconnect.get() and self.rpc_wanted and not self.rpc_connected and not self.rpc_busy and now>=self.rpc_retry_at:
            self.rpc_retry_at=now+30; self.log('Discord: automatic reconnect attempt'); self.rpc_connect(automatic=True)
        last=datetime.datetime.fromtimestamp(self.last_success).strftime('%H:%M:%S') if self.last_success else 'Never'
        next_poll=('%ds'%max(0,int(self.next_poll_at-now))) if self.next_poll_at is not None else ('In progress' if self.busy else 'Not scheduled')
        self.prune_usage()
        wall=time.time()
        usage='\nRequests: %d / min | %d / hour | %d / 24h'%(sum(t>wall-60 for t in self.request_times),sum(t>wall-3600 for t in self.request_times),len(self.request_times))
        usage+='\n24h budget: '+self.daily_budget.get()+' | '+('access restricted' if self.restricted else 'No access restriction recorded')
        usage+='\nRate headers: '+('; '.join(k+': '+v for k,v in self.limit_headers.items()) or 'Not supplied by server')
        self.diagnostics.set('Last successful HTTP response: '+last+'\nLast request duration: '+('%.2f s'%self.last_latency if self.last_latency is not None else 'Unknown')+'\nNext poll: '+next_poll+'\nCurrent interval: %ds | Failed requests this session: %d'%(self.poll_seconds(),self.total_errors)+'\nCooldown remaining: %ds'%max(0,int(self.block_until-now))+'\nLast Discord acknowledgment: '+('%.0f ms'%(self.rpc_last_ack*1000) if self.rpc_last_ack is not None else 'Unknown')+usage)
        expiry=token_expiry(self.normalized_token())
        if self.expiry_notice.get() and expiry and 0<expiry-time.time()<=1800 and self.notice_key!=expiry:
            self.notice_key=expiry; self.log('Account: access token expires within 30 minutes')
            if self.tray_icon:
                try: self.tray_icon.notify('Access token expires within 30 minutes.','SoundCloud Presence')
                except Exception: pass
            else: messagebox.showinfo('SoundCloud account','Your access token expires within 30 minutes. Renew it from the Accounts tab.',parent=self.root)
        try:
            while True:
                event=self.tray_events.get_nowait()
                if event=='show': self.show_window()
                elif event=='exit': self.exit_app(); return
                elif event=='pause': self.pause()
                elif event=='start': self.start()
        except queue.Empty: pass
        self.root.after(500,self.maintenance)
    def save_preset(self):
        name=simpledialog.askstring('Save preset','Preset name:',initialvalue=self.preset_name.get(),parent=self.root)
        if not name or not name.strip(): return
        name=name.strip()
        if name in self.presets and not messagebox.askyesno('Replace preset','Replace '+name+'?',parent=self.root): return
        self.presets[name]={'fields':{k:v.get() for k,v in self.custom_fields.items()},'elapsed':self.custom_elapsed.get()}
        self.preset_name.set(name); self.preset_combo.configure(values=sorted(self.presets)); self.save_settings(); self.log('Custom preset saved: '+name)
    def load_preset(self):
        data=self.presets.get(self.preset_name.get())
        if not data: return
        for k,v in self.custom_fields.items():
            if k in data.get('fields',{}): v.set(data['fields'][k])
        self.custom_elapsed.set(data.get('elapsed',False)); self.custom_started=None
        self.log('Custom preset loaded; click Publish custom to apply')
    def delete_preset(self):
        name=self.preset_name.get()
        if name in self.presets and messagebox.askyesno('Delete preset','Delete '+name+'?',parent=self.root):
            del self.presets[name]; self.preset_name.set(''); self.preset_combo.configure(values=sorted(self.presets)); self.save_settings()
    def export_settings(self):
        target=filedialog.asksaveasfilename(parent=self.root,defaultextension='.json',filetypes=[('JSON settings','*.json')])
        if not target: return
        try:
            self.save_settings(); Path(target).write_text(self.config_path.read_text(encoding='utf-8'),encoding='utf-8'); self.log('Settings exported (no account token)')
        except OSError as e: self.log('Settings export failed: '+str(e))
    def open_log_folder(self):
        try:
            self.config_path.parent.mkdir(parents=True,exist_ok=True); os.startfile(str(self.config_path.parent))
        except (OSError,AttributeError): self.log('Could not open log folder')
    def hide_to_tray(self):
        if self.tray_icon: self.root.withdraw(); return
        try:
            import pystray
            from PIL import Image, ImageDraw
            img=Image.new('RGBA',(64,64),(28,32,39,255)); draw=ImageDraw.Draw(img)
            for x,h in [(12,14),(20,24),(28,38),(36,46),(44,30),(52,18)]: draw.rounded_rectangle((x,32-h//2,x+4,32+h//2),radius=2,fill=(255,113,56,255))
            menu=pystray.Menu(pystray.MenuItem('Open',lambda icon,item:self.tray_events.put('show'),default=True),pystray.MenuItem('Pause polling',lambda icon,item:self.tray_events.put('pause')),pystray.MenuItem('Resume polling',lambda icon,item:self.tray_events.put('start')),pystray.MenuItem('Exit',lambda icon,item:self.tray_events.put('exit')))
            self.tray_icon=pystray.Icon('SoundCloudPresence',img,'SoundCloud Presence',menu)
            def setup(icon):
                icon.visible=True; self.tray_events.put('hidden')
            self.tray_icon.run_detached(setup=setup)
            self.root.after(500,self.finish_hide)
        except Exception as e:
            self.tray_icon=None; self.log('Tray unavailable: '+str(e)); messagebox.showinfo('System tray','Install optional tray support: python -m pip install pystray==0.19.5 Pillow==9.5.0',parent=self.root)
    def finish_hide(self):
        if self.tray_icon and self.tray_icon.visible: self.root.withdraw()
        else: self.log('Tray icon did not become visible; window kept open')
    def show_window(self):
        self.root.deiconify(); self.root.lift()
    def close(self):
        if self.tray_enabled.get(): self.hide_to_tray()
        else: self.exit_app()
    def exit_app(self):
        self.closing=True
        if self.tray_icon:
            try: self.tray_icon.stop()
            except Exception: pass
        self.save_settings(); self.running=False; self.cancel(); self.rpc_commands.put(('quit',None))
        self.root.after(500,self.root.destroy)
    def retry(self,msg):
        self.failures+=1; delay=min(300,30*2**min(self.failures-1,4)); self.block_until=time.monotonic()+delay
        self.log(msg+'; cooldown %d seconds'%delay); self.status.set(msg+'; retry in %d seconds'%delay); self.schedule(delay)

if __name__=='__main__':
    root=tk.Tk(); Tester(root); root.mainloop()
