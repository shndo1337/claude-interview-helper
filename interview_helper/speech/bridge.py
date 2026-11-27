"""JSON-line transport for the local Electron speech and clipboard bridge."""
import json
import os
import queue
import subprocess
import threading
import uuid

from ..paths import ELECTRON, ELECTRON_BRIDGE


class SpeechBridge:
    def __init__(self,log,platform='x11'):
        env=os.environ.copy();env.pop('ELECTRON_RUN_AS_NODE',None)
        self.process=subprocess.Popen([str(ELECTRON),f'--ozone-platform={platform}','--class=claude-interview-status',str(ELECTRON_BRIDGE)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,bufsize=1,env=env)
        self.lock=threading.Lock();self.pending={};self.ready=threading.Event();self.failure=None
        threading.Thread(target=self.reader,daemon=True).start()

    def reader(self):
        for line in self.process.stdout:
            try:msg=json.loads(line)
            except ValueError:continue
            if msg.get('event')=='ready':self.ready.set()
            elif msg.get('event')=='error':self.failure=msg.get('error');self.ready.set()
            elif msg.get('id') in self.pending:self.pending[msg['id']].put(msg)
        self.failure='Речевой помощник остановился';self.ready.set()

    def request(self,action,timeout=30,**args):
        token=uuid.uuid4().hex;q=queue.Queue()
        with self.lock:
            if self.process.poll() is not None:raise RuntimeError(self.failure or 'Помощник не запущен')
            self.pending[token]=q
            self.process.stdin.write(json.dumps({'id':token,'action':action,**args},ensure_ascii=False)+'\n')
            self.process.stdin.flush()
        try:
            msg=q.get(timeout=timeout)
            if msg.get('error'):raise RuntimeError(msg['error'])
            return msg.get('result')
        finally:self.pending.pop(token,None)

    def status(self,text):
        try:self.request('status',text=text,timeout=3)
        except Exception:pass

    def close(self):
        try:self.process.stdin.write('{"action":"exit"}\n');self.process.stdin.flush();self.process.wait(timeout=4)
        except Exception:self.process.terminate()
