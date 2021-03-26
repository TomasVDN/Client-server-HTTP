import mimetypes
import os
import select
import socket
import sys
import threading

#TODO timestamp

from email.utils import formatdate
from urllib.parse import urlparse


def empty_socket(sock):
    input = [sock]
    while 1:
        inputready, o, e = select.select(input, [], [], 0.0)
        if len(inputready) == 0: break
        for s in inputready: s.recv(1)


class Separator:
    def __init__(self, connection):
        self.body = b""
        self.code = None
        self.encoding = "ISO-8859-1"
        self.content_length = None
        self.is_chunked = False

        self.header = self.receive_header(connection)
        if self.is_chunked:
            self.receive_chunked_body(connection)
        elif self.content_length is not None:
            self.receive_content_length_body(connection)

    def receive_header(self, connection):
        data = bytes()
        chunk = bytes()

        # Receive header, and decode
        while b'\r\n\r\n' not in data:
            chunk = connection.recv(1)
            if not chunk: break
            data += chunk

        data = data.decode()

        # Get return code
        self.code = data.split("\r\n")[0].split(" ")[1]

        # Change header to a dict
        header_elements = {k: v.strip() for k, v in
                           [line.split(":", 1) for line in data.splitlines() if ":" in line]}

        # Check all useful header fields
        for key in header_elements:
            if key == "Content-Length":
                self.content_length = int(header_elements[key])
            if key == "Transfer-Encoding":
                self.is_chunked = True

        return data.encode()

    def receive_chunked_body(self, connection):
        while True:
            # Get length of the next chunk
            length = bytes()
            while b'\r\n' not in length:
                length += connection.recv(1)
            length = int(length.decode(self.encoding), 16)

            # If last chunk has been read, return. Otherwise, read content of chunk
            if length == 0:
                connection.recv(2)
                return
            remaining = length + 2  # +2 for \r\n

            chunk = b''
            while remaining > 0:
                if remaining > 1024:
                    part = connection.recv(1024)
                else:
                    part = connection.recv(remaining)

                chunk += part
                remaining -= len(part)

            self.body += chunk

    def receive_content_length_body(self, connection, chunk_size=1024):
        data = bytes()

        length = self.content_length
        while length > 0:
            if length > chunk_size:
                part = connection.recv(chunk_size)
            else:
                part = connection.recv(length)
            data += part
            length -= len(part)

        self.body = data
        empty_socket(connection)

class Request:
    def __init__(self, header, body):
        self.method = None
        self.uri = None
        self.http_version = "1.1" # default to HTTP/1.1
        self.keep_connection = True # default to True (HTTP/1.1)
        self.encoding = "iso-8859-1"
        self.is_host_present = False
        self.if_modified_since = False
        self.date = None

        self.header = self.parse(header)
        self.body = body

    def parse(self, data):
        lines = data.split(b"\r\n")
        request_line = lines[0]
        words = request_line.split(b" ")

        self.method = words[0].decode()
        self.uri = words[1].decode() #uri
        if "http" in self.uri: # absolute url
            parsed = urlparse(self.uri)
            self.uri = parsed.path

        self.http_version = words[2]

        header_elements = { k:v.strip() for k,v in [line.split(":",1) for line in data.decode().splitlines() if ":" in line]}

        for key in header_elements:
            if key == "Host":
                self.is_host_present = True
            elif key == "Connection":
                if header_elements[key] == "close":
                    self.keep_connection = False
            elif key == "Content-Type":
                if "utf-8" in header_elements[key]:
                    self.encoding = "utf-8"
                elif "iso-8859-1" in header_elements[key]:
                    self.encoding = "iso-8859-1"


def end_removal(value, a):
    pos_a = value.rfind(a)
    if pos_a == -1: return ""
    return value[0:pos_a]

class Server:
    def __init__(self, host='127.0.0.1', port=9000):
        self.host = host
        self.port = port

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(5)

        print("Listening at", sock.getsockname())

        while True:
            conn, address = sock.accept()
            print("Connection initiated with:", address)
            thread = ServerThread(conn, address)
            thread.start()


class ServerThread(threading.Thread):
    def __init__(self, connection, address):
        threading.Thread.__init__(self)
        self.connection = connection
        self.address = address

    def run(self):
        keep_connection = True
        while (keep_connection):
            try:
                sep = Separator(self.connection)
            except Exception:
                break

            request = Request(sep.header, sep.body)

            try:
                if not request.is_host_present:
                    handler = self.handle_400
                else:
                    handler = getattr(self, 'handle_%s' % request.method)
            except AttributeError:
                handler = self.handle_501

            response = handler(request)

            self.connection.send(response)

            if not request.keep_connection:
                self.connection.close()
                print("Closed connection with:", self.address)
                sys.exit()

        self.connection.close()
        print("Closed connection with:", self.address)
        sys.exit()

    headers = {
        'Server': 'Localhost',
        'Content-Type': 'text/html',
        'Content-Length': '0',
    }

    status_codes = {
        200: 'OK',
        201: 'Created',
        304: 'Not Modified',
        400: 'Bad Request',
        404: 'Not Found',
        501: 'Not Implemented',
    }

    def response_line(self, status_code):
        """Returns response line"""
        reason = self.status_codes[status_code]
        line = "HTTP/1.1 %s %s\r\n" % (status_code, reason)

        return line.encode()  # call encode to convert str to bytes

    def response_headers(self, extra_headers=None):
        headers_copy = self.headers.copy()  # make a local copy of headers

        if extra_headers:
            headers_copy.update(extra_headers)

        headers = ""

        for h in headers_copy:
            headers += "%s: %s\r\n" % (h, headers_copy[h])

        headers += "Date: %s\r\n" % (formatdate(timeval=None, localtime=False, usegmt=True))

        return headers.encode()  # call encode to convert str to bytes

    def handle_501(self, request):
            response_line = self.response_line(status_code=501)

            blank_line = b"\r\n"

            response_body = b"<h1>501 Not implemented</h1>"

            extra_headers = {'Content-Length': len(response_body)}
            response_headers = self.response_headers(extra_headers)

            return b"".join([response_line, response_headers, blank_line, response_body, blank_line, blank_line])

    def handle_400(self, request):
            response_line = self.response_line(status_code=400)

            blank_line = b"\r\n"

            response_body = b"<h1>400 Bad request</h1>"

            extra_headers = {'Content-Length': len(response_body)}
            response_headers = self.response_headers(extra_headers)

            return b"".join([response_line, response_headers, blank_line, response_body, blank_line, blank_line])

    def handle_GET(self, request):
        filename = request.uri.strip('/')  # remove the slash from the request URI

        if os.path.exists(filename):
            response_line = self.response_line(status_code=200)

            # find out a file's MIME type
            # if nothing is found, just send `text/html`
            content_type = mimetypes.guess_type(filename)[0] or 'text/html'

            with open(filename, 'rb') as f:
                response_body = f.read()
        else:
            response_line = self.response_line(status_code=404)
            content_type = 'text/html'
            response_body = b"<h1>404 Not Found</h1>"

        blank_line = b"\r\n"

        extra_headers = {'Content-Type': content_type, 'Content-Length': len(response_body)}
        response_headers = self.response_headers(extra_headers)

        return b"".join([response_line, response_headers, blank_line, response_body, blank_line, blank_line])

    def handle_HEAD(self, request):
        filename = request.uri.strip('/')  # remove the slash from the request URI

        if os.path.exists(filename):
            response_line = self.response_line(status_code=200)

            # find out a file's MIME type
            # if nothing is found, just send `text/html`
            content_type = mimetypes.guess_type(filename)[0] or 'text/html'

            extra_headers = {'Content-Type': content_type}
            response_headers = self.response_headers(extra_headers)

        else:
            response_line = self.response_line(status_code=404)
            response_headers = self.response_headers()

        blank_line = b"\r\n"

        return b"".join([response_line, response_headers, blank_line, blank_line])

    def handle_PUT(self, request):
        return_path = "documents" + request.uri
        filename = "documents" + request.uri

        directory = os.path.dirname(filename)
        if not os.path.exists(directory):
            os.makedirs(directory)

        with open(filename, 'w') as out:
            print(request.body.decode())
            out.write(request.body.decode(request.encoding))
            out.close()

        response_line = self.response_line(status_code=200)

        blank_line = b"\r\n"

        extra_headers = {'Content-Location': return_path}
        response_headers = self.response_headers(extra_headers)

        return b"".join([response_line, response_headers, blank_line, blank_line])

    def handle_POST(self, request):
        return_path = "documents" + request.uri
        filename = "documents" + request.uri

        if os.path.isfile(filename):
            with open(filename, 'a') as out:
                out.write(request.body.decode(request.encoding))
                out.close()
            response_line = self.response_line(status_code=200)
        else:
            directory = os.path.dirname(filename)
            if not os.path.exists(directory):
                os.makedirs(directory)

            with open(filename, 'w') as out:
                out.write(request.body.decode(request.encoding))
                out.close()

            response_line = self.response_line(status_code=201)

        blank_line = b"\r\n"

        extra_headers = {'Content-Location': return_path}
        response_headers = self.response_headers(extra_headers)

        return b"".join([response_line, response_headers, blank_line, blank_line])

if __name__ == '__main__':
    server = Server()
    server.start()