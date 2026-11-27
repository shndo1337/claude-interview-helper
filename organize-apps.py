#!/usr/bin/env python3
"""Group KDE and local helper launchers in one GNOME folder / Plasma menu.

Executable programs stay in place. User-level desktop entry overrides, menu
merges and GNOME folder settings are backed up before modification.
"""
import configparser
import datetime
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from gi.repository import Gio

ROOT=Path(__file__).resolve().parent
HOME=Path.home()
APPS=HOME/'.local/share/applications'
CATEGORY='X-PlasmaTools'
FOLDER='plasma-tools'
TITLE='Plasma и инструменты'
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
BACKUP=ROOT/'runtime/menu-layout-backups'/stamp
BACKUP.mkdir(parents=True,mode=0o700)
manifest={'files':{},'gnomeFolders':{}}


def backup(path):
    key=str(path)
    if key in manifest['files']:return
    row={'existed':path.exists() or path.is_symlink()}
    if row['existed']:
        if path.is_symlink():row['symlink']=os.readlink(path)
        row['mode']=stat.S_IMODE(path.stat().st_mode)
        target=BACKUP/'files'/path.relative_to(HOME)
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(path.read_bytes())
    manifest['files'][key]=row
    (BACKUP/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))


def write(path,text):
    backup(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.plasma-menu-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as file:file.write(text)
        os.chmod(temp,0o644);os.replace(temp,path)
    finally:Path(temp).unlink(missing_ok=True)


def categorize(text):
    lines=text.splitlines();out=[];entry=False;found=False
    for line in lines:
        if line.startswith('['):entry=line.strip()=='[Desktop Entry]'
        if entry and line.startswith('Categories='):
            out.append('Categories='+CATEGORY+';');found=True
        else:out.append(line)
    if not found:
        out.insert(out.index('[Desktop Entry]')+1,'Categories='+CATEGORY+';')
    return '\n'.join(out)+'\n'


def collect():
    directories=[Path(p)/'applications' for p in os.environ.get('XDG_DATA_DIRS','/usr/local/share:/usr/share').split(':')]
    for base in [Path('/usr/share/applications'),Path('/var/lib/snapd/desktop/applications')]:
        if base not in directories:directories.append(base)
    effective={}
    for base in [*reversed(directories),APPS]:
        for file in base.glob('*.desktop'):effective[file.name]=file
    chosen={}
    for name,file in effective.items():
        config=configparser.ConfigParser(interpolation=None,strict=False);config.optionxform=str
        try:config.read(file)
        except configparser.Error:continue
        if 'Desktop Entry' not in config:continue
        entry=config['Desktop Entry']
        if entry.get('Hidden','').lower()=='true' or entry.get('NoDisplay','').lower()=='true':continue
        categories=entry.get('Categories','').split(';')
        only=set(filter(None,entry.get('OnlyShowIn','').split(';')))
        if (name.startswith(('org.kde.','org.interviewhelper.ClaudeInterview','org.interviewhelper.CodexInterview')) or 'KDE' in categories
            or CATEGORY in categories or only=={'KDE'}
            or name in ['systemsettings.desktop','kdesystemsettings.desktop','xtigervncviewer.desktop','meetingly-local.desktop']):
            chosen[name]=file
    return chosen


def main():
    for backend in ['Claude','Codex']:
        for suffix in ['','Workspace']:
            backup(APPS/f'org.interviewhelper.{backend}Interview{suffix}.desktop')
    subprocess.run(['/usr/bin/python3',str(ROOT/'install-launcher.py')],check=True)
    chosen=collect()
    for name,file in chosen.items():write(APPS/name,categorize(file.read_text()))
    directory='interview-helper-plasma-tools.directory'
    write(HOME/'.local/share/desktop-directories'/directory,
          '[Desktop Entry]\nType=Directory\nName='+TITLE+'\nIcon=preferences-desktop\n')
    for filename in ['plasma-applications.menu','gnome-applications.menu']:
        target=HOME/'.config/menus'/filename
        if target.exists():
            root=ET.fromstring(target.read_text(),parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
        else:
            root=ET.Element('Menu');ET.SubElement(root,'Name').text='Applications'
            ET.SubElement(root,'MergeFile').text='/etc/xdg/menus/'+filename
        for child in list(root.findall('Menu')):
            if child.findtext('Name')=='PlasmaTools':root.remove(child)
        menu=ET.SubElement(root,'Menu');ET.SubElement(menu,'Name').text='PlasmaTools'
        ET.SubElement(menu,'Directory').text=directory
        ET.SubElement(ET.SubElement(menu,'Include'),'Category').text=CATEGORY
        ET.indent(root)
        write(target,'<!DOCTYPE Menu PUBLIC "-//freedesktop//DTD Menu 1.0//EN" "http://www.freedesktop.org/standards/menu-spec/1.0/menu.dtd">\n'+ET.tostring(root,encoding='unicode')+'\n')
    parent=Gio.Settings.new('org.gnome.desktop.app-folders')
    children=list(parent.get_strv('folder-children'));manifest['folderChildren']=children
    settings={}
    for folder in [*children,*([] if FOLDER in children else [FOLDER])]:
        config=Gio.Settings.new_with_path('org.gnome.desktop.app-folders.folder',
                                        '/org/gnome/desktop/app-folders/folders/'+folder+'/')
        settings[folder]=config
        manifest['gnomeFolders'][folder]={key:config.get_value(key).unpack()
            for key in ['apps','categories','excluded-apps','name','translate']}
    # Persist the backup before changing any dconf values.
    (BACKUP/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    ids=set(chosen)
    for folder,config in settings.items():
        config.delay()
        if folder==FOLDER:
            config.set_string('name',TITLE);config.set_boolean('translate',False)
            config.set_strv('apps',sorted(ids));config.set_strv('categories',[CATEGORY])
            config.set_strv('excluded-apps',[])
        else:
            config.set_strv('apps',[a for a in config.get_strv('apps') if a not in ids])
            config.set_strv('excluded-apps',sorted(set(config.get_strv('excluded-apps'))|ids))
        config.apply()
    if FOLDER not in children:parent.set_strv('folder-children',children+[FOLDER])
    Gio.Settings.sync()
    if shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database',str(APPS)],check=True)
    if shutil.which('kbuildsycoca6'):
        env=os.environ.copy();env.update(XDG_MENU_PREFIX='plasma-',XDG_CURRENT_DESKTOP='KDE')
        subprocess.run(['kbuildsycoca6','--noincremental'],env=env,check=True)
    print(json.dumps({'folder':TITLE,'launchers':len(chosen),'backup':str(BACKUP)},ensure_ascii=False))


if __name__=='__main__':main()
