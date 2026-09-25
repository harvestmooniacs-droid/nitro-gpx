#!/usr/bin/env python3
"""Background music for Nitro GFX (adapted from the Lina tool's gui_audio.py).

Uses `miniaudio` (a small Python binding around the public-domain
miniaudio.c library) to decode and play the bundled MP3 directly - it does
NOT depend on any codec/media component installed on the user's Windows
(unlike the old approach based on the Windows MCI "mpegvideo" driver, which
is missing on N/KN editions and some minimal/server installs and silently
left the Music button disabled there). If playback still cannot start for
some other reason (no audio device, etc.) the tool keeps working normally -
the Music button just stays disabled.
"""
import array
import threading
from pathlib import Path

try:
    import miniaudio
except Exception:
    miniaudio = None


class Bgm:
    def __init__(self, mp3_path, alias=None):
        self.path = Path(mp3_path)
        self.ok = False
        self.playing = False
        self._device = None
        self._paused = False
        self._raw = b''
        self._frame_size = 4     # 16-bit stereo = 2 channels * 2 bytes
        self._total_frames = 0

        if miniaudio is None or not self.path.exists():
            return
        try:
            sf = miniaudio.decode_file(str(self.path))   # -> 16-bit stereo 44100 Hz PCM
            self._raw = sf.samples.tobytes() if isinstance(sf.samples, array.array) else bytes(sf.samples)
            self._total_frames = len(self._raw) // self._frame_size
            self.ok = self._total_frames > 0
        except Exception:
            self.ok = False

    def _generator(self):
        """Feeds the playback device raw PCM frames, looping forever. While
        paused it yields silence instead of stopping the device (simpler and
        glitch-free vs. tearing the device down and rebuilding it)."""
        raw, total, fsize = self._raw, self._total_frames, self._frame_size
        pos = 0
        frame_count = yield b''      # miniaudio primes the generator with an empty yield first
        while True:
            if self._paused or total == 0:
                frame_count = yield bytes(frame_count * fsize)
                continue
            end = pos + frame_count
            if end <= total:
                chunk = raw[pos * fsize:end * fsize]
                pos = end if end < total else 0
            else:
                chunk = raw[pos * fsize:total * fsize]
                remaining = frame_count - (total - pos)
                chunk += raw[0:remaining * fsize]
                pos = remaining
            frame_count = yield chunk

    def play(self):
        if not self.ok or self.playing:
            return
        # Creating the WASAPI device (miniaudio.PlaybackDevice) initializes
        # COM on whatever thread calls it. Doing that on the main/GUI
        # thread collides with Tkinter's native folder/file dialogs on
        # Windows (filedialog.askdirectory etc also need COM there),
        # which then hang the whole window ("Not Responding") the next
        # time a dialog is opened. Do the device creation on its own
        # thread so the GUI thread's COM state is never touched by it.
        self.playing = True   # optimistic; _play_impl flips it back on failure
        threading.Thread(target=self._play_impl, daemon=True).start()

    def _play_impl(self):
        try:
            gen = self._generator()
            next(gen)   # prime it up to the first `yield` (required by miniaudio)
            device = miniaudio.PlaybackDevice()
            device.start(gen)
            self._device = device
            self._paused = False
        except Exception:
            # some machines have no usable audio device at all - fail quietly,
            # same as when the file itself could not be decoded
            self.ok = False
            self.playing = False

    def pause(self):
        if not self.ok:
            return
        self._paused = True
        self.playing = False

    def resume(self):
        if not self.ok:
            return
        if self._device is None:
            self.play()
        else:
            self._paused = False
            self.playing = True

    def toggle(self):
        if self.playing:
            self.pause()
        else:
            self.resume()
        return self.playing

    def close(self):
        if self._device is not None:
            try:
                self._device.stop()
                self._device.close()
            except Exception:
                pass
            self._device = None
        self.playing = False
