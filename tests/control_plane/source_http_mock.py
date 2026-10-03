"""Explicit fake subprocess pipes; runs only mock HTTP worker code in-process."""
from contextlib import contextmanager
import json
import os
from types import SimpleNamespace
from unittest import mock

REAL_JSON_LOADS = json.loads
REAL_OS_READ = os.read
REAL_OS_WRITE = os.write
REAL_SET_BLOCKING = os.set_blocking


@contextmanager
def mock_source_process(transport):
    """Exercise the actual supervisor with fake fds and a pure mock HTTP worker."""
    processes = []
    pipes = {}
    next_fd = 100000
    class Pipe:
        def __init__(self, label, process):
            nonlocal next_fd
            next_fd += 1
            self.fd = next_fd
            self.label = label
            self.process = process
            self.data = bytearray()
            self.closed = False
            pipes[self.fd] = self
        def fileno(self):
            return self.fd
        def close(self):
            if not self.closed and self.label == 'stdin':
                # Separate process JSON parsing is represented independently of
                # any JSON parser mock installed for the parent's HTTP body.
                with mock.patch.object(transport.json, 'loads', REAL_JSON_LOADS):
                    try:
                        request = transport.parse_input(bytes(self.data))
                        body = transport.perform_request(request)
                        self.process.stdout.data.extend(transport.ok_envelope(body))
                    except transport.SourceHttpError as error:
                        self.process.stdout.data.extend(transport.error_envelope(error))
                self.process.returncode = 0
            self.closed = True
    class Process:
        def __init__(self, args, kwargs):
            self.args = args
            self.kwargs = kwargs
            self.pid = 900001 + len(processes)
            self.returncode = None
            self.waits = []
            self.stdin = Pipe('stdin', self)
            self.stdout = Pipe('stdout', self)
            self.stderr = Pipe('stderr', self)
        def wait(self, *, timeout):
            self.waits.append(timeout)
            if self.returncode is None:
                raise AssertionError('fake worker was not completed')
            return self.returncode
    class Selector:
        def __init__(self):
            self.keys = {}
            self.closed = False
        def register(self, pipe, events, label):
            self.keys[pipe.fd] = SimpleNamespace(fileobj=pipe, data=label, events=events)
        def unregister(self, pipe):
            del self.keys[pipe.fd]
        def get_map(self):
            return self.keys
        def select(self, timeout):
            return [(key, key.events) for key in list(self.keys.values())
                    if key.data == 'stdin' or key.fileobj.process.returncode is not None]
        def close(self):
            self.closed = True
    def popen(*args, **kwargs):
        process = Process(args[0], kwargs)
        processes.append(process)
        return process
    def read(fd, size):
        if fd not in pipes:
            return REAL_OS_READ(fd, size)
        pipe = pipes[fd]
        data = bytes(pipe.data[:size])
        del pipe.data[:size]
        return data
    def write(fd, data):
        if fd not in pipes:
            return REAL_OS_WRITE(fd, data)
        pipes[fd].data.extend(data)
        return len(data)
    def blocking(fd, value):
        if fd not in pipes:
            return REAL_SET_BLOCKING(fd, value)
    def killpg(pid, signum):
        process = next(p for p in processes if p.pid == pid)
        process.returncode = -signum
    with mock.patch.object(transport.subprocess, 'Popen', side_effect=popen), \
            mock.patch.object(transport.selectors, 'DefaultSelector', Selector), \
            mock.patch.object(transport.os, 'read', side_effect=read), \
            mock.patch.object(transport.os, 'write', side_effect=write), \
            mock.patch.object(transport.os, 'set_blocking', side_effect=blocking), \
            mock.patch.object(transport.os, 'killpg', side_effect=killpg):
        yield processes
