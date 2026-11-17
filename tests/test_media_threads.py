"""Prove the persisted media thread budget reaches real FFmpeg commands."""
import math
from pathlib import Path
import subprocess
import wave

import pytest
from app import engines


@pytest.mark.parametrize('count',[1,4])
def test_media_thread_budget_reaches_real_encoder(tmp_path,monkeypatch,count):
    source=tmp_path/'tone.wav'
    with wave.open(str(source),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000)
        stream.writeframes(b'\0\0'*800)
    out=tmp_path/'out';out.mkdir()
    monkeypatch.setenv('FORMAT_HUB_MEDIA_THREADS',str(count))
    calls=[];original=engines._run
    def capture(args,cwd=None):
        calls.append(args)
        return original(args,cwd)
    monkeypatch.setattr(engines,'_run',capture)
    result=engines._media('media_audio',[source],out,{'target':'mp3'})
    assert result[0].stat().st_size>0
    thread_values=[str(calls[0][i+1]) for i,arg in enumerate(calls[0][:-1]) if arg=='-threads']
    assert thread_values==[str(count),str(count)]
    decoded=subprocess.run([engines._ffmpeg(),'-v','error','-i',str(result[0]),'-f','null','-'],capture_output=True,timeout=20)
    assert decoded.returncode==0,decoded.stderr
