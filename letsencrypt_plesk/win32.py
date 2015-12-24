"""Polyfill for Windows support"""

import os
import sys
from ctypes import *
from ctypes.wintypes import *
from requests.packages.urllib3 import connection as urllib3_connection
from requests.packages.urllib3 import util as urllib3_util

kernel32 = WinDLL('kernel32')

if sys.version_info < (3, 0, 0):  # pragma: no cover
    import _winreg as winreg  # pylint: disable=import-error
else:
    import winreg  # pylint: disable=import-error

if not getattr(__builtins__, "WindowsError", None):
    class WindowsError(OSError):
        pass


def get_plesk_config(variable, default=None):
    """Retrieve Plesk specific variable from winreg."""
    explorer = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                              "Software\\PLESK\\PSA Config\\Config")
    try:
        i = 0
        while 1:
            name, value, key_type = winreg.EnumValue(explorer, i)
            if key_type == winreg.REG_DWORD or key_type == winreg.REG_SZ:
                if variable == name:
                    return str(value)
            i += 1
    except WindowsError:
        pass
    return default


def ssl_wrap_localhost_no_sni(*args, **kwargs):
    if 'server_hostname' in kwargs and \
       '127.0.0.1' == kwargs['server_hostname']:
            orig_has_sni = urllib3_util.HAS_SNI
            urllib3_util.HAS_SNI = False
            try:
                return urllib3_util.ssl_wrap_socket(*args, **kwargs)
            finally:
                urllib3_util.HAS_SNI = orig_has_sni
    return orig_ssl_wrap(*args, **kwargs)
orig_ssl_wrap = urllib3_connection.ssl_wrap_socket
urllib3_connection.ssl_wrap_socket = ssl_wrap_localhost_no_sni

try:
    from os import geteuid  # pylint: disable=unused-import
except ImportError:
    os.geteuid = lambda: 0


def os_symlink(source, link_name):
    """Create symlink using win32 API directly."""
    csl = kernel32.CreateSymbolicLinkW
    csl.argtypes = (c_wchar_p, c_wchar_p, c_uint32)
    csl.restype = c_ubyte
    flags = 1 if os.path.isdir(source) else 0
    if csl(link_name, source, flags) == 0:
        raise WinError()
try:
    from os import symlink  # pylint: disable=unused-import
except ImportError:
    os.symlink = os_symlink


# Original http://stackoverflow.com/questions/27972776/
# having-trouble-implementing-a-readlink-function

LPDWORD = POINTER(DWORD)
UCHAR = c_ubyte

GetFileAttributesW = kernel32.GetFileAttributesW
GetFileAttributesW.restype = DWORD
GetFileAttributesW.argtypes = (LPCWSTR,)  # lpFileName In

INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
FILE_ATTRIBUTE_REPARSE_POINT = 0x00400

CreateFileW = kernel32.CreateFileW
CreateFileW.restype = HANDLE
CreateFileW.argtypes = (LPCWSTR,  # lpFileName In
                        DWORD,    # dwDesiredAccess In
                        DWORD,    # dwShareMode In
                        LPVOID,   # lpSecurityAttributes In_opt
                        DWORD,    # dwCreationDisposition In
                        DWORD,    # dwFlagsAndAttributes In
                        HANDLE)   # hTemplateFile In_opt

CloseHandle = kernel32.CloseHandle
CloseHandle.restype = BOOL
CloseHandle.argtypes = (HANDLE,)  # hObject In

INVALID_HANDLE_VALUE = HANDLE(-1).value
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000

DeviceIoControl = kernel32.DeviceIoControl
DeviceIoControl.restype = BOOL
DeviceIoControl.argtypes = (HANDLE,   # hDevice In
                            DWORD,    # dwIoControlCode In
                            LPVOID,   # lpInBuffer In_opt
                            DWORD,    # nInBufferSize In
                            LPVOID,   # lpOutBuffer Out_opt
                            DWORD,    # nOutBufferSize In
                            LPDWORD,  # lpBytesReturned Out_opt
                            LPVOID)   # lpOverlapped Inout_opt

FSCTL_GET_REPARSE_POINT = 0x000900A8
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
IO_REPARSE_TAG_SYMLINK = 0xA000000C
MAXIMUM_REPARSE_DATA_BUFFER_SIZE = 0x4000


class GENERIC_REPARSE_BUFFER(Structure):
    _fields_ = (('DataBuffer', UCHAR * 1),)


class SYMBOLIC_LINK_REPARSE_BUFFER(Structure):
    _fields_ = (('SubstituteNameOffset', USHORT),
                ('SubstituteNameLength', USHORT),
                ('PrintNameOffset', USHORT),
                ('PrintNameLength', USHORT),
                ('Flags', ULONG),
                ('PathBuffer', WCHAR * 1))

    @property
    def PrintName(self):
        arrayt = WCHAR * (self.PrintNameLength // 2)
        offset = type(self).PathBuffer.offset + self.PrintNameOffset
        return arrayt.from_address(addressof(self) + offset).value


class MOUNT_POINT_REPARSE_BUFFER(Structure):
    _fields_ = (('SubstituteNameOffset', USHORT),
                ('SubstituteNameLength', USHORT),
                ('PrintNameOffset', USHORT),
                ('PrintNameLength', USHORT),
                ('PathBuffer', WCHAR * 1))

    @property
    def PrintName(self):
        arrayt = WCHAR * (self.PrintNameLength // 2)
        offset = type(self).PathBuffer.offset + self.PrintNameOffset
        return arrayt.from_address(addressof(self) + offset).value


class REPARSE_DATA_BUFFER(Structure):
    class REPARSE_BUFFER(Union):
        _fields_ = (('SymbolicLinkReparseBuffer',
                     SYMBOLIC_LINK_REPARSE_BUFFER),
                    ('MountPointReparseBuffer',
                        MOUNT_POINT_REPARSE_BUFFER),
                    ('GenericReparseBuffer',
                        GENERIC_REPARSE_BUFFER))
    _fields_ = (('ReparseTag', ULONG),
                ('ReparseDataLength', USHORT),
                ('Reserved', USHORT),
                ('ReparseBuffer', REPARSE_BUFFER))
    _anonymous_ = ('ReparseBuffer',)


def os_islink(path):
    result = GetFileAttributesW(path)
    if result == INVALID_FILE_ATTRIBUTES:
        raise WinError()
    return bool(result & FILE_ATTRIBUTE_REPARSE_POINT)


def os_readlink(path):
    reparse_point_handle = CreateFileW(path,
                                       0,
                                       0,
                                       None,
                                       OPEN_EXISTING,
                                       FILE_FLAG_OPEN_REPARSE_POINT |
                                       FILE_FLAG_BACKUP_SEMANTICS,
                                       None)
    if reparse_point_handle == INVALID_HANDLE_VALUE:
        raise WinError()
    target_buffer = c_buffer(MAXIMUM_REPARSE_DATA_BUFFER_SIZE)
    n_bytes_returned = DWORD()
    io_result = DeviceIoControl(reparse_point_handle,
                                FSCTL_GET_REPARSE_POINT,
                                None, 0,
                                target_buffer, len(target_buffer),
                                byref(n_bytes_returned),
                                None)
    CloseHandle(reparse_point_handle)
    if not io_result:
        raise WinError()
    rdb = REPARSE_DATA_BUFFER.from_buffer(target_buffer)
    if rdb.ReparseTag == IO_REPARSE_TAG_SYMLINK:
        return rdb.SymbolicLinkReparseBuffer.PrintName
    elif rdb.ReparseTag == IO_REPARSE_TAG_MOUNT_POINT:
        return rdb.MountPointReparseBuffer.PrintName
    raise ValueError("not a link")
try:
    from os import readlink  # pylint: disable=unused-import
except ImportError:
    os.readlink = os_readlink


def os_realpath(fpath):
    while os_islink(fpath):
        rpath = os_readlink(fpath)
        if not os.path.isabs(rpath):
            rpath = os.path.abspath(
                os.path.join(os.path.dirname(fpath), rpath))
        fpath = rpath
    return fpath

try:
    from os import realpath  # pylint: disable=unused-import
except ImportError:
    os.realpath = os_realpath
