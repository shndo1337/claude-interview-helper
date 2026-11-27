#!/usr/bin/env python3
"""Exercise actual PipeWire capture -> live ASR -> draft callback, without Claude.

The two test sinks and microphone source are private virtual nodes. No physical
microphone is recorded, no sound is played on speakers, no defaults are changed.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import time
from types import MethodType, SimpleNamespace
import uuid

from interview_helper.controller import Controller
from interview_helper.paths import ROOT, RUNTIME
from interview_helper.speech.bridge import SpeechBridge


def nodes():
    return {obj.get('info', {}).get('props', {}).get('node.name')
            for obj in json.loads(subprocess.check_output(['pw-dump'], text=True))}


def wait_for(names, processes):
    until = time.monotonic() + 8
    while time.monotonic() < until:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError('Virtual audio process stopped')
        if set(names) <= nodes():
            return
        time.sleep(.1)
    raise RuntimeError('Virtual audio nodes not available')


def main():
    suffix = uuid.uuid4().hex[:8]
    mic_sink, mic_source, system_sink = [f'claude_asr_test_{name}_{suffix}' for name in ['mic_sink', 'mic_source', 'system_sink']]
    virtual = []
    bridge = controller = None
    report = {}
    sample = ROOT.parent/'meetingly-local/fleurs-russian.wav'
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    with (RUNTIME/'streaming-pipeline.log').open('w') as log, ThreadPoolExecutor(max_workers=1) as pool:
        try:
            for name in [mic_sink, system_sink]:
                props = {'factory.name':'support.null-audio-sink', 'node.name':name,
                         'media.class':'Audio/Sink', 'audio.position':['MONO'],
                         'node.virtual':True, 'priority.session':-1000, 'object.linger':False}
                process = subprocess.Popen(['pw-cli'],stdin=subprocess.PIPE,stdout=log,stderr=log,text=True)
                process.stdin.write('create-node adapter '+json.dumps(props)+'\n')
                process.stdin.flush()
                virtual.append(process)
            wait_for([mic_sink, system_sink], virtual)
            virtual.append(subprocess.Popen([
                'pw-loopback', '--name=claude-asr-test', '--channels=1', '--latency=50',
                '--capture-props='+json.dumps({'stream.capture.sink':True,'target.object':mic_sink,'node.passive':True}),
                '--playback-props='+json.dumps({'node.name':mic_source,'media.class':'Audio/Source',
                                                'node.virtual':True,'priority.session':-1000,'node.passive':True}),
            ],stdout=log,stderr=log))
            wait_for([mic_source], virtual)
            bridge = SpeechBridge(log,platform='headless')
            if not bridge.ready.wait(30) or bridge.failure:
                raise RuntimeError(bridge.failure or 'Recognizer did not load')
            states, drafts = [], []
            controller = SimpleNamespace(runtime=RUNTIME,bridge=bridge,log=log,pool=pool,capture=None,capture_dir=None,
                                         capture_timer=None,live_audio=None,recording_id=None)
            controller.audio_node = lambda kind: mic_source if kind=='SOURCE' else system_sink
            controller.state = lambda state,**extra: states.append({'state':state,**extra})
            controller.insert = lambda text: drafts.append(text)
            for name in ['start_capture','stop_capture','erase_capture','voice','dispatch']:
                setattr(controller,name,MethodType(getattr(Controller,name),controller))
            for label,sink in [('system',system_sink),('microphone',mic_sink)]:
                controller.voice()
                assert controller.live_audio.thread.is_alive()
                time.sleep(.5)
                subprocess.run(['pw-play','--target',sink,str(sample)],stdout=log,stderr=log,check=True,timeout=25)
                controller.voice()
                assert len(drafts[-1])>80, 'Speech missing: '+str(states[-1])
                assert states[-1]['recognizer']=='Nemotron 3.5 Streaming'
                assert controller.capture_dir is None and controller.live_audio is None
                report[label]={**states[-1],'draft':drafts[-1]}
                print(label,json.dumps(report[label],ensure_ascii=False),flush=True)
            report['temporaryRecordingsDeleted']=True
            report['noClaudeRequests']=True
            report['ok']=True
        finally:
            if controller:
                if controller.capture: controller.stop_capture()
                if controller.live_audio: controller.live_audio.cancel()
                controller.erase_capture()
            if bridge: bridge.close()
            for process in reversed(virtual):
                if process.poll() is None: process.terminate()
                try: process.wait(timeout=4)
                except subprocess.TimeoutExpired: process.kill();process.wait()
    assert not {mic_sink,mic_source,system_sink} & nodes(), 'Temporary nodes left behind'
    report['temporaryAudioNodesDeleted']=True
    (ROOT/'streaming-pipeline-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('streaming-pipeline: passed',flush=True)


if __name__=='__main__':
    main()
