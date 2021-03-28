import mimetypes
import os
import select
import shutil
import socket
import sys

# Import BeautifulSoup, used to find the images.
from bs4 import BeautifulSoup

##############################

# Class used to process the request. Parses the input arguments (argv), and compiles the correct message to send to the
# web servers
class Request:

    # In the init, process the input arguments. Expects at least a method and an URL. If no port is given, default to 80
    def __init__(self, list_of_input_element):

        # Method
        self.method = list_of_input_element[0]

        # Host & resource. If no resource is given, set it to /.
        pos = list_of_input_element[1].find("/")
        if pos == -1:
            self.host = list_of_input_element[1]
            self.resource = "/"
        else:
            self.host = list_of_input_element[1][0:pos]
            self.resource = list_of_input_element[1][pos:]

        # Port
        if len(list_of_input_element) == 3:
            self.port = list_of_input_element[2]
        else:
            self.port = 80

        self.make_request()

    # Compiles the method, host and resource into a valid HTTP request.
    def make_request(self):
        self.request = '{} {} HTTP/1.1\r\nHost: {}\r\n'.format(self.method, self.resource, self.host).encode()

        request_body = None

        # Appends Content type and length to the header & reads the body.
        if self.method == "PUT" or self.method == "POST":

            file = input("Please, enter a file to send:\n")
            if os.path.exists(file):
                # find out a file's MIME type
                # if nothing is found, just send `text/html`
                content_type = mimetypes.guess_type(file)[0] or 'text/html'

                with open(file, 'rb') as f:
                    request_body = f.read()
            else:
                raise Exception

            self.request += 'Content-Type: {}\r\nContent-Length: {}\r\n'.format(content_type, len(request_body)).encode()

        # Appends the asked file to the body (if there is one)
        if request_body:
            self.request += '\r\n'.encode() + request_body

        # End of request
        self.request += '\r\n'.encode()

    # Used to change the resource of the request (for images)
    def change_resource(self, method, resource):
        self.method = method
        self.resource = resource
        self.make_request()

##############################


# Remove the data present on the socket
def empty_socket(sock):
    input = [sock]
    while 1:
        input_ready, o, e = select.select(input, [], [], 0.0)
        if len(input_ready) == 0:
            break
        for s in input_ready: s.recv(1)


# Class used to receive and process the response.
class Response:

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

        # Receive header, and decode
        while b'\r\n\r\n' not in data:
            chunk = connection.recv(1)
            if not chunk: break
            data += chunk

        data = data.decode()

        # Get status code
        self.code= data.split("\r\n")[0].split(" ")[1]

        # Change header elements to a dict
        header_elements = {k: v.strip() for k, v in
                           [line.split(":", 1) for line in data.splitlines() if ":" in line]}

        # Check all useful header fields
        for key in header_elements:
            if key == "Content-Type":
                if "UTF-8" in header_elements[key]:
                    self.encoding = "UTF-8"
                elif "ISO-8859-1" in header_elements[key]:
                    self.encoding = "ISO-8859-1"
            if key == "Content-Length":
                self.content_length = int(header_elements[key])
            if key == "Transfer-Encoding":
                self.is_chunked = True

        return data

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

            # Read chunk
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
    def receive_content_length_body(self, connection, chunk_size = 1024):
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


# Function to import the images from a HTML file. Uses BeautifulSoup to parse the HTML. The folder structure remains almost
# the same as on the server.
def import_images(connection, request, response):
    soup = BeautifulSoup(response.body.decode(response.encoding), "html.parser")

    # Find all images
    images = soup.findAll('img')

    # Send a GET request to get all images.
    for image in images:
        src = image['src']

        if src[0] != "/":
            src = "/" + src

        request.change_resource("GET", src)

        # Send request
        print("[SENDING]\n" + request.request.decode())
        connection.sendall(request.request)

        # Receive response
        response = Response(connection)
        print("[RECEIVED]\n" + response.header)

        # Check if directories exists. If not, create it.
        directory = "output" + os.path.dirname(src)
        if not os.path.exists(directory):
            os.makedirs(directory)

        with open("output/" + src, "wb") as out:
            out.write(response.body)
            out.close()

        if src[0] == "/": src = src[1:]
        image['src'] = src

    # Do the same as above, but with lowsrc.
    for image in images:
        if image.has_attr("lowsrc"):
            src = image['lowsrc']

            if src[0] != "/":
                src = "/" + src

            request.change_resource("GET", src)

            # Send request
            print("[SENDING]\n" + request.request.decode())
            connection.sendall(request.request)

            # Receive response
            response = Response(connection)
            print("[RECEIVED]\n" + response.header)

            directory = "output" + os.path.dirname(src)
            if not os.path.exists(directory):
                os.makedirs(directory)

            # Write to file
            with open("output/" + src, "wb") as out:
                out.write(response.body)
                out.close()

            if src[0] == "/": src = src[1:]
            image['src'] = src

    # Write html to output/output.html
    with open("output/output.html", "w") as out:
        out.write(str(soup))
        out.close()

##############################


def main(list):
    # Clean the output folder
    with os.scandir("output") as entries:
        for entry in entries:
            if entry.is_file():
                os.remove(entry.path)
            elif entry.is_dir():
                shutil.rmtree(entry.path)

    # Make a request
    request = Request(list)

    # Initiate socket connection
    ip = socket.gethostbyname(request.host)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((ip, int(request.port)))

    # Send Request
    print("[SENDING]\n" + request.request.decode())
    sock.sendall(request.request)

    # Receive response
    response = Response(sock)
    print("[RECEIVED]\n" + response.header)

    # If method is GET and everything happened as intended, get images.
    if request.method == "GET" and int(response.code) == 200:
        import_images(sock, request, response)

    sock.close()

if __name__ == '__main__':
    main(sys.argv[1:])