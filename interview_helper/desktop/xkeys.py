"""XTest paste for the dedicated XWayland Claude window; no global input daemon."""
import ctypes as C
import ctypes.util
import time


class XKeys:
    def __init__(self):
        self.x=C.CDLL(ctypes.util.find_library('X11'))
        self.xt=C.CDLL(ctypes.util.find_library('Xtst'))
        self.x.XOpenDisplay.argtypes=[C.c_char_p];self.x.XOpenDisplay.restype=C.c_void_p
        self.x.XKeysymToKeycode.argtypes=[C.c_void_p,C.c_ulong];self.x.XKeysymToKeycode.restype=C.c_uint
        self.x.XQueryKeymap.argtypes=[C.c_void_p,C.c_char_p]
        self.x.XFlush.argtypes=[C.c_void_p]
        self.xt.XTestFakeKeyEvent.argtypes=[C.c_void_p,C.c_uint,C.c_int,C.c_ulong]
        self.display=self.x.XOpenDisplay(None)
        if not self.display:raise RuntimeError('Не удалось подключиться к XWayland')

    def code(self,sym):return self.x.XKeysymToKeycode(self.display,sym)

    def released(self):
        modifiers=[self.code(n) for n in [0xffe1,0xffe2,0xffe3,0xffe4,0xffe9,0xffea,0xffeb,0xffec]]
        for _ in range(60):
            bits=C.create_string_buffer(32);self.x.XQueryKeymap(self.display,bits)
            if not any(bits.raw[c//8] & (1<<(c%8)) for c in modifiers):return
            time.sleep(.025)
        raise RuntimeError('Отпусти Ctrl / Alt / Shift и повтори хоткей')

    def paste(self):
        self.released()
        control=self.code(0xffe3);v=self.code(ord('v'))
        for key,pressed in [(control,1),(v,1),(v,0),(control,0)]:
            self.xt.XTestFakeKeyEvent(self.display,key,pressed,0)
        self.x.XFlush(self.display)
