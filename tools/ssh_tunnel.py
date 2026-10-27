"""Binary stdin/stdout tunnel for OpenSSH ProxyCommand; no shell or credentials."""
import argparse
import os
import re
import socket
import ssl
import sys
import threading
from urllib.parse import urlsplit


def parse_proxy(value):
    if not isinstance(value,str)or any(c in value for c in '\r\n\x00'):raise ValueError('Некорректный адрес прокси')
    p=urlsplit(value if '://'in value else'http://'+value)
    if p.scheme not in {'http','https','socks5','socks5h'}or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in {'','/'}:
        raise ValueError('Нужен HTTP или SOCKS5 прокси без учётных данных в адресе')
    if not re.fullmatch(r'[A-Za-z0-9._:-]+',p.hostname)or(p.port is not None and not 1<=p.port<=65535):raise ValueError('Некорректный адрес прокси')
    return p


def receive(sock,count):
    out=b''
    while len(out)<count:
        part=sock.recv(count-len(out))
        if not part:raise OSError('Прокси закрыл соединение во время подключения')
        out+=part
    return out


def connect(value,host,port):
    if not host or any(c in host for c in '\r\n\x00')or not 1<=port<=65535:raise ValueError('Некорректный адрес SSH')
    proxy=parse_proxy(value);sock=socket.create_connection((proxy.hostname,proxy.port or(1080 if proxy.scheme.startswith('socks')else 443 if proxy.scheme=='https'else 80)),timeout=12)
    try:
        if proxy.scheme=='https':sock=ssl.create_default_context().wrap_socket(sock,server_hostname=proxy.hostname)
        if proxy.scheme.startswith('socks'):
            sock.sendall(b'\x05\x01\x00')
            if receive(sock,2)!=b'\x05\x00':raise OSError('SOCKS5 прокси не принимает подключение без авторизации')
            name=host.encode('idna')
            if len(name)>255:raise ValueError('Слишком длинное имя SSH-сервера')
            sock.sendall(b'\x05\x01\x00\x03'+bytes([len(name)])+name+port.to_bytes(2,'big'))
            reply=receive(sock,4)
            if reply[:2]!=b'\x05\x00':raise OSError('SOCKS5 прокси отклонил соединение')
            length={1:4,4:16}.get(reply[3])
            if reply[3]==3:length=receive(sock,1)[0]
            if length is None:raise OSError('Некорректный ответ SOCKS5')
            receive(sock,length+2)
        else:
            address=('['+host+']'if':'in host else host)+':'+str(port)
            sock.sendall(('CONNECT '+address+' HTTP/1.1\r\nHost: '+address+'\r\n\r\n').encode('ascii'))
            header=b''
            while not header.endswith(b'\r\n\r\n'):
                header+=receive(sock,1)
                if len(header)>16384:raise OSError('Слишком большой ответ прокси')
            first=header.split(b'\r\n',1)[0].split()
            if len(first)<2 or first[1]!=b'200':raise OSError('HTTP прокси не разрешил SSH CONNECT')
        sock.settimeout(None);return sock
    except BaseException:sock.close();raise


def main():
    args=argparse.ArgumentParser();args.add_argument('--proxy',required=True);args.add_argument('--host',required=True);args.add_argument('--port',type=int,required=True);options=args.parse_args()
    with connect(options.proxy,options.host,options.port)as sock:
        def upload():
            try:
                while data:=os.read(sys.stdin.fileno(),65536):sock.sendall(data)
                sock.shutdown(socket.SHUT_WR)
            except OSError:pass
        threading.Thread(target=upload,daemon=True).start()
        while data:=sock.recv(65536):sys.stdout.buffer.write(data);sys.stdout.buffer.flush()


if __name__=='__main__':
    try:main()
    except (OSError,ValueError):
        # Do not echo proxy URLs; they must never become credential-bearing logs.
        sys.stderr.write('Granka SSH proxy: connection failed before SSH authentication.\n');raise SystemExit(1)
