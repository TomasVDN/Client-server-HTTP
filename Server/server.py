import mimetypes
import os
import select
import socket
import sys
import threading
from datetime import datetime
import time
from urllib.parse import urlparse


##############################


# Remove the data present on the socket
def empty_socket(sock):
    input = [sock]
    while 1:
        inputready, o, e = select.select(input, [], [], 0.0)
        if len(inputready) == 0: break
        for s in inputready: s.recv(1)


# Class used to receive and separate the header from the body.
class Separator:
    # Function to initiate all defaults and read the header. When header has been read, call the correct body receiver
    # method (chunked or with content length)
    def __init__(self, connection):
        self.body = b""
        self.code = None
        self.encoding = "ISO-8859-1"
        self.content_length = None
        self.is_chunked = False

        # Read and process header.
        self.header = self.receive_header(connection)

        # Call correct body method reader. If no body is expected, no one is called.
        if self.is_chunked:
            self.receive_chunked_body(connection)
        elif self.content_length is not None:
            self.receive_content_length_body(connection)

    # Function to receive header, and process interesting information.
    def receive_header(self, connection):
        data = bytes()
        chunk = bytes()

        # Receive header, and decode
        while b'\r\n\r\n' not in data:
            chunk = connection.recv(1)
            if not chunk: break
            data += chunk

        data = data.decode()

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

    # Function to receive chunked bodies.
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

    # Function to receive a body with content_length. Receives in chunks of 1024 by default, until there is less than
    # 1024 bytes of data left.
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


##############################


# Class used to process the incoming request. Parses the header to extract all the useful information.
class Request:
    # Function to initiate all defaults and read the header
    def __init__(self, header, body):
        self.method = None
        self.uri = None
        self.http_version = "HTTP/1.1"
        self.keep_connection = True  # default to True (HTTP/1.1)
        self.encoding = "iso-8859-1"
        self.is_host_present = False
        self.if_modified_since = None
        self.if_unmodified_since = None

        self.header = header
        self.parse(header)
        self.body = body

    # Parses the header, to find all the useful headers.
    def parse(self, data):
        lines = data.split(b"\r\n")
        request_line = lines[0]
        words = request_line.split(b" ")

        # Method
        self.method = words[0].decode()

        # Resource
        self.uri = words[1].decode()  # uri
        if "http" in self.uri:  # absolute url
            parsed = urlparse(self.uri)
            self.uri = parsed.path

        # HTTP version
        self.http_version = words[2].decode()

        # Change header elements to a dict
        header_elements = {k: v.strip() for k, v in
                           [line.split(":", 1) for line in data.decode().splitlines() if ":" in line]}

        # Check all useful header fields
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
            elif key == "If-Modified-Since":
                self.if_modified_since = datetime.strptime(header_elements[key], "%a, %d %b %Y %I:%M:%S GMT")
            elif key == "If-Unmodified-Since":
                self.if_unmodified_since = datetime.strptime(header_elements[key], "%a, %d %b %Y %I:%M:%S GMT")


##############################


# Main class. This server checks a port for incoming connections. If there is one, makes a serverThread to
# process further interactions
class Server:
    def __init__(self, host='127.0.0.1', port=9000):
        self.host = host
        self.port = port

    def start(self):
        # Make connection, and listen to port.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(5)

        print("Listening at", sock.getsockname())

        # While True, listen to port, and make thread there is an incoming connection.
        while True:
            conn, address = sock.accept()
            print("Connection initiated with:", address)
            thread = ServerThread(conn, address)
            thread.start()


##############################


# ServerThread class. This class defines everything needed to further handle the connection with a client.
# It processes all the information.
class ServerThread(threading.Thread):
    def __init__(self, connection, address):
        threading.Thread.__init__(self)
        self.connection = connection
        self.address = address

    def run(self):
        keep_connection = True

        # Eternal loop, until client closes or asks to close the connection.
        while keep_connection:

            # Try receiving. If an error occurs, it means the connection has been closed on the client side.
            # In that case, break while loop.
            try:
                sep = Separator(self.connection)
            except Exception:
                break

            # Process request.
            try:
                request = Request(sep.header, sep.body)
            except Exception:
                break

            # Get the correct handler for the given method. Also checks for errors (HTTP version, Host,...).
            try:
                if not request.http_version == "HTTP/1.1":
                    handler = self.handle_505
                elif not request.is_host_present:
                    handler = self.handle_400
                else:
                    handler = getattr(self, 'handle_%s' % request.method)
            except AttributeError:
                handler = self.handle_501

            # Make response
            try:
                response = handler(request)
            except Exception:
                response = self.handle_500()

            # Send response
            self.connection.send(response)

            # If connection: close header has been sent, close the connection.
            if not request.keep_connection:
                self.connection.close()
                print("Closed connection with:", self.address)
                sys.exit()

        # If while loop has been broken, client has closed his side of the connection. In that case, close
        # our side.
        self.connection.close()
        print("Closed connection with:", self.address)
        sys.exit()

    # default headers.
    headers = {
        'Server': 'Localhost',
    }

    status_codes = {
        200: 'OK',  # Standard successful response
        201: 'Created',  # Response if file has been created
        304: 'Not Modified',  # Response if file has not been modified (GET,HEAD, If-Modified-Since)
        400: 'Bad Request',  # Host not present
        404: 'Not Found',  # File not found
        412: 'Precondition Failed',  # Response if file has been modified (PUT, POST, If-Unmodified-Since)
        500: 'Internal Server Error',  # Response if there was an error while processing the intended response
        501: 'Not Implemented',  # Response for unimplemented methods (DELETE, OPTIONS,...)
        505: 'HTTP Version Not Supported',  # Response for unsupported HTTP versions
    }

    # Make a response line.
    def response_line(self, status_code):
        reason = self.status_codes[status_code]
        line = "HTTP/1.1 %s %s\r\n" % (status_code, reason)

        return line.encode()

    # Process the headers.
    def response_headers(self, extra_headers=None):
        # make a local copy of headers
        headers_copy = self.headers.copy()

        # Merge extra headers and default headers
        if extra_headers:
            headers_copy = {**headers_copy, **extra_headers}

        headers = ""

        for h in headers_copy:
            headers += "{}: {}\r\n".format(h, headers_copy[h])

        # Add Date to headers.
        headers += "Date: " + time.strftime("%a, %d %b %Y %I:%M:%S", time.gmtime()) + " GMT\r\n"

        return headers.encode()

    # 50 handler (Internal Server Error).
    def handle_500(self, request):
        response_line = self.response_line(status_code=500)

        blank_line = b"\r\n"

        response_body = b"<h1>500 Internal Server Error</h1>"

        extra_headers = {'Content-Type': 'text/html', 'Content-Length': len(response_body)}
        response_headers = self.response_headers(extra_headers)

        return b"".join([response_line, response_headers, blank_line, response_body, blank_line, blank_line])

    # 501 handler (Not implemented).
    def handle_501(self, request):
        response_line = self.response_line(status_code=501)

        blank_line = b"\r\n"

        response_body = b"<h1>501 Not implemented</h1>"

        extra_headers = {'Content-Type': 'text/html', 'Content-Length': len(response_body)}
        response_headers = self.response_headers(extra_headers)

        return b"".join([response_line, response_headers, blank_line, response_body, blank_line, blank_line])

    # 505 handler (Not supported HTTP version).
    def handle_505(self, request):
        response_line = self.response_line(status_code=505)

        blank_line = b"\r\n"

        response_body = b"<h1>HTTP Version Not Supported</h1>"

        extra_headers = {'Content-Type': 'text/html', 'Content-Length': len(response_body)}
        response_headers = self.response_headers(extra_headers)

        return b"".join([response_line, response_headers, blank_line, response_body, blank_line, blank_line])

    # 400 handler (Bad request (called when Host is absent)).
    def handle_400(self, request):
        response_line = self.response_line(status_code=400)

        blank_line = b"\r\n"

        response_body = b"<h1>400 Bad request</h1>"

        extra_headers = {'Content-Type': 'text/html', 'Content-Length': len(response_body)}
        response_headers = self.response_headers(extra_headers)

        return b"".join([response_line, response_headers, blank_line, response_body, blank_line, blank_line])

    # GET handler.
    def handle_GET(self, request):
        filename = request.uri.strip('/')  # remove the slash from the request URI

        # Check if file exists
        if os.path.exists(filename):
            dt = os.path.getmtime(filename)
            modification_time = datetime.fromtimestamp(dt)

            # Check modification date for the If-Modified-Since header
            if request.if_modified_since:
                if modification_time < request.if_modified_since:
                    response_line = self.response_line(status_code=304)
                    blank_line = b"\r\n"

                    response_headers = self.response_headers()
                    return b"".join([response_line, response_headers, blank_line, blank_line])

            response_line = self.response_line(status_code=200)

            # find out a file's MIME type
            # if nothing is found, just send `text/html`
            content_type = mimetypes.guess_type(filename)[0] or 'text/html'

            # Read file to body.
            with open(filename, 'rb') as f:
                response_body = f.read()
        else:
            # If file not found
            response_line = self.response_line(status_code=404)
            content_type = 'text/html'
            response_body = b"<h1>404 Not Found</h1>"

        blank_line = b"\r\n"

        # Make extra headers, and make header.
        extra_headers = {'Content-Type': content_type, 'Content-Length': len(response_body)}
        response_headers = self.response_headers(extra_headers)

        return b"".join([response_line, response_headers, blank_line, response_body, blank_line, blank_line])

    # HEAD handler
    def handle_HEAD(self, request):
        filename = request.uri.strip('/')  # remove the slash from the request URI

        # Check if file exists
        if os.path.exists(filename):
            dt = os.path.getmtime(filename)
            modification_time = datetime.fromtimestamp(dt)

            # Check modification date for the If-Modified-Since header
            if request.if_modified_since:
                if modification_time < request.if_modified_since:
                    response_line = self.response_line(status_code=304)
                    blank_line = b"\r\n"

                    response_headers = self.response_headers()
                    return b"".join([response_line, response_headers, blank_line, blank_line])

            response_line = self.response_line(status_code=200)

            # find out a file's MIME type
            # if nothing is found, just send `text/html`
            content_type = mimetypes.guess_type(filename)[0] or 'text/html'

            extra_headers = {'Content-Type': content_type}
            response_headers = self.response_headers(extra_headers)

        else:
            # If file not found
            response_line = self.response_line(status_code=404)
            response_headers = self.response_headers()

        blank_line = b"\r\n"

        return b"".join([response_line, response_headers, blank_line, blank_line])

    # PUT handler. Received documents will be put into the documents folder.
    def handle_PUT(self, request):
        return_path = "documents" + request.uri
        filename = "documents" + request.uri

        # Checks if file exists.
        if os.path.exists(filename):
            dt = os.path.getmtime(filename)
            modification_time = datetime.fromtimestamp(dt)

            # Check modification date for the If-Unmodified-Since header
            if request.if_unmodified_since:
                if modification_time > request.if_unmodified_since:
                    response_line = self.response_line(status_code=412)
                    blank_line = b"\r\n"

                    response_headers = self.response_headers()
                    return b"".join([response_line, response_headers, blank_line, blank_line])

        # Make directories if they do not exist.
        directory = os.path.dirname(filename)
        if not os.path.exists(directory):
            os.makedirs(directory)

        # (Over)Write file
        with open(filename, 'w') as out:
            print(request.body.decode())
            out.write(request.body.decode(request.encoding))
            out.close()

        response_line = self.response_line(status_code=200)

        blank_line = b"\r\n"

        extra_headers = {'Content-Location': return_path}
        response_headers = self.response_headers(extra_headers)

        return b"".join([response_line, response_headers, blank_line, blank_line])

    # POST handler. Received documents will be put into the documents folder.
    def handle_POST(self, request):
        return_path = "documents" + request.uri
        filename = "documents" + request.uri

        # Checks if file exists.
        if os.path.exists(filename):
            dt = os.path.getmtime(filename)
            modification_time = datetime.fromtimestamp(dt)

            # Check modification date for the If-Unmodified-Since header
            if request.if_unmodified_since:
                if modification_time > request.if_unmodified_since:
                    response_line = self.response_line(status_code=412)
                    blank_line = b"\r\n"

                    response_headers = self.response_headers()
                    return b"".join([response_line, response_headers, blank_line, blank_line])

        # Check if file already exists. If yes, append. Otherwise, create new one.
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

##############################


if __name__ == '__main__':
    server = Server()
    server.start()
