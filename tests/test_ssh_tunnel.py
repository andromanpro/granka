import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlunsplit
from tools.ssh_tunnel import connect,parse_proxy,receive
from tools.site_wordpress import WordPress


class ProxyTests(unittest.TestCase):
    def server(self,fn):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();self.addCleanup(listener.close);errors=[]
        def serve():
            try:
                with listener.accept()[0]as sock:sock.settimeout(5);fn(sock)
            except Exception as e:errors.append(e)
        worker=threading.Thread(target=serve,daemon=True);worker.start();return listener.getsockname()[1],worker,errors

    def test_http_tunnel_preserves_all_binary_bytes_and_half_close(self):
        payload=bytes(range(256))*4
        def server(sock):
            header=b''
            while not header.endswith(b'\r\n\r\n'):header+=receive(sock,1)
            self.assertTrue(header.startswith(b'CONNECT host.example.invalid:22 HTTP/1.1'))
            sock.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n');data=b''
            while part:=sock.recv(4096):data+=part
            self.assertEqual(data,payload);sock.sendall(data[::-1])
        port,worker,errors=self.server(server)
        result=subprocess.run([sys.executable,str(Path(__file__).parents[1]/'tools/ssh_tunnel.py'),'--proxy',f'http://127.0.0.1:{port}','--host','host.example.invalid','--port','22'],input=payload,capture_output=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        worker.join(3);self.assertFalse(errors);self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout,payload[::-1])

    def test_socks_handshake_keeps_server_banner(self):
        def server(sock):
            self.assertEqual(receive(sock,3),b'\x05\x01\x00');sock.sendall(b'\x05\x00');self.assertEqual(receive(sock,4),b'\x05\x01\x00\x03');name=receive(sock,receive(sock,1)[0]);self.assertEqual(name,b'host.example.invalid');self.assertEqual(receive(sock,2),b'\x00\x16');sock.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x16SSH-banner')
        port,worker,errors=self.server(server)
        with connect(f'socks5h://127.0.0.1:{port}','host.example.invalid',22)as sock:self.assertEqual(receive(sock,10),b'SSH-banner')
        worker.join(3);self.assertFalse(errors)

    def test_bad_addresses_and_credentials_are_rejected(self):
        for value in [urlunsplit(('http','user:secret@localhost:1','','','')),'ftp://localhost:1','http://localhost:1/?secret=x','http://localhost\n:1','http://localhost&command:1']:
            with self.subTest(value=value),self.assertRaises(ValueError):parse_proxy(value)

    def test_wordpress_explicit_system_proxy_and_missing_proxy(self):
        env={'staging':{'ssh':['user@host.example.invalid'],'wp':['wp'],'url':'https://staging.example.invalid','ssh_proxy':'system'}}
        with tempfile.TemporaryDirectory()as folder:
            wp=WordPress('staging',folder,env)
            with patch('tools.site_wordpress.urllib.request.getproxies',return_value={'https':'http://127.0.0.1:10809'}),patch('tools.site_wordpress.subprocess.run',return_value=subprocess.CompletedProcess([],0,b'ok',b''))as call:
                self.assertEqual(wp.ssh('read-only'),'ok');args=call.call_args.args[0];self.assertTrue(any(v.startswith('ProxyCommand=')for v in args));self.assertIn('10809',' '.join(args))
            with patch('tools.site_wordpress.urllib.request.getproxies',return_value={}),self.assertRaisesRegex(ValueError,'не настроен'):wp.ssh('read-only')
