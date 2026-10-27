import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from tools.document_store import DocumentStore


class AtomicSaveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'article.granka.json'
        self.path.write_text(json.dumps({'versions':[{'id':'v1','text':'Original'}],'annotations':[]}),encoding='utf-8')
        self.store=DocumentStore(lambda:{},threading.RLock())
        self.before=self.path.read_bytes()
        self.revision=self.store.revision(self.path)

    @staticmethod
    def denied(code=5):
        error=PermissionError('Windows denied replacement')
        error.winerror=code
        return error

    def save(self,fn=None):
        return self.store.mutate(self.path,self.revision,'once',fn or (lambda d:d.update(title='Saved')))

    def test_transient_windows_denial_retries_only_replace_once(self):
        real=Path.replace;attempts=[];calls=[]
        def replace(source,target):
            attempts.append(1)
            if len(attempts)<3:raise self.denied()
            return real(source,target)
        with patch('tools.document_store._WINDOWS',True),patch.object(Path,'replace',replace),patch('tools.document_store.time.sleep'):
            status,_=self.save(lambda d:(calls.append(1),d.update(title='Saved')))
        self.assertEqual(status,200);self.assertEqual(len(attempts),3);self.assertEqual(calls,[1])
        self.assertEqual(json.loads(self.path.read_text())['ops'],['once'])

    def test_persistent_denial_preserves_document_and_cleans_temp(self):
        with patch('tools.document_store._WINDOWS',True),patch.object(Path,'replace',side_effect=self.denied())as replace,patch('tools.document_store.time.sleep'):
            with self.assertRaises(PermissionError):self.save()
        self.assertEqual(replace.call_count,8)
        self.assertEqual(self.path.read_bytes(),self.before)
        self.assertEqual(list(self.path.parent.glob('*.tmp')),[])

    def test_concurrent_change_during_wait_is_a_conflict(self):
        external=json.loads(self.before);external['versions'][0]['text']='Other editor'
        def external_save(_):self.path.write_text(json.dumps(external),encoding='utf-8')
        with patch('tools.document_store._WINDOWS',True),patch.object(Path,'replace',side_effect=self.denied(32)),patch('tools.document_store.time.sleep',side_effect=external_save):
            status,result=self.save()
        self.assertEqual(status,409);self.assertEqual(result['error'],'conflict')
        self.assertEqual(json.loads(self.path.read_text()),external)

    def test_other_errors_fail_immediately(self):
        with patch('tools.document_store._WINDOWS',True),patch.object(Path,'replace',side_effect=self.denied(1314))as replace:
            with self.assertRaises(PermissionError):self.save()
        self.assertEqual(replace.call_count,1);self.assertEqual(self.path.read_bytes(),self.before)

    @unittest.skipUnless(os.name=='nt','Windows sharing semantics')
    def test_real_windows_reader_without_delete_share_is_temporary(self):
        import ctypes as c
        from ctypes import wintypes as w
        kernel=c.WinDLL('kernel32',use_last_error=True)
        kernel.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
        kernel.CreateFileW.restype=w.HANDLE;kernel.CloseHandle.argtypes=[w.HANDLE]
        handle=kernel.CreateFileW(str(self.path),0x80000000,3,None,3,0,None)
        self.assertNotEqual(handle,c.c_void_p(-1).value)
        timer=threading.Timer(.18,lambda:kernel.CloseHandle(handle));timer.start()
        try:
            status,_=self.save()
            self.assertEqual(status,200)
        finally:timer.join()
