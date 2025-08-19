# youtube_downloader_A.M.py
# VidScoop M.A — full-featured: hidden queue, playlist full-download, thumbnail per-download,
# popup progress, pause/resume/cancel, clipboard monitor, ffmpeg discovery.
# Requires: yt_dlp, customtkinter, Pillow, requests, pyperclip

import os
import sys
import time
import re
import threading
import shutil
import ctypes
from io import BytesIO
from tkinter import filedialog

import customtkinter as ctk
import yt_dlp
import requests
from PIL import Image, ImageTk
import pyperclip


from PIL import Image
img = Image.open("logo.png")
img.save("app.ico")

# ---------------- hide console on Windows (optional) ----------------
def hide_console():
    if os.name == "nt":
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
        except Exception:
            pass

# If you want console visible while debugging, comment next line:
hide_console()

# ---------------- ffmpeg discovery ----------------
def find_ffmpeg():
    search = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        search += [
            os.path.join(exe_dir, "ffmpeg.exe"),
            os.path.join(exe_dir, "_internal", "ffmpeg.exe"),
        ]
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        search += [
            os.path.join(script_dir, "ffmpeg.exe"),
            os.path.join(script_dir, "_internal", "ffmpeg.exe"),
        ]
    if hasattr(sys, "_MEIPASS"):
        search += [
            os.path.join(sys._MEIPASS, "ffmpeg.exe"),
            os.path.join(sys._MEIPASS, "_internal", "ffmpeg.exe"),
        ]
    search += [
        os.path.join(os.getcwd(), "ffmpeg.exe"),
        os.path.join(os.getcwd(), "_internal", "ffmpeg.exe"),
    ]
    for p in search:
        if p and os.path.isfile(p):
            print(f"✅ FFmpeg found at: {p}")
            return p
    print("❌ FFmpeg not found.")
    return None

FFMPEG_PATH = find_ffmpeg()
FFMPEG_EXISTS = bool(FFMPEG_PATH) or (shutil.which("ffmpeg") is not None)

# ---------------- App setup ----------------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

app = ctk.CTk()
app.title("VidScoop M.A | Ve1.0.0")
app.geometry("820x620")
app.resizable(False, False)

# ---------------- Globals ----------------
queue_lock = threading.Lock()
# download_queue now stores dict items: {'type': 'video'/'playlist', 'url': str}
download_queue = []
worker_thread = None
is_downloading = False
pause_flag = False
stop_flag = False
last_temp_filename = None
color_activated = False
last_clipboard = ""

# supported platforms
supported_url_re = re.compile(
    r"(https?://)?(www\.)?(youtube\.com|youtu\.be|tiktok\.com|vimeo\.com|facebook\.com|instagram\.com)",
    re.IGNORECASE,
)

# progress popup references
progress_popup = None
popup_progress_bar = None
popup_progress_label = None

# ---------------- UI ----------------
# Top: URL + Add
top = ctk.CTkFrame(app)
top.pack(padx=10, pady=8, fill="x")
ctk.CTkLabel(top, text="Video / Playlist URL:", text_color="white").pack(anchor="w")

url_entry = ctk.CTkEntry(top, width=680, placeholder_text="Paste link here")
url_entry.pack(pady=6, side="left", padx=(0,8))

def add_to_queue():
    url = url_entry.get().strip()
    if not url:
        set_status("❌ Enter a valid link.")
        return
    if not supported_url_re.search(url):
        set_status("⚠️ The link is not supported.")
        return

    # detect playlist vs single via yt_dlp extract_info (not downloading)
    info, err = fetch_info_quiet(url)
    added = 0
    if info and info.get("entries"):
        # It's a playlist — add the playlist URL as one queue item (so it downloads full)
        with queue_lock:
            download_queue.append({"type": "playlist", "url": url})
        added = 1
    else:
        # single video
        with queue_lock:
            download_queue.append({"type": "video", "url": url})
        added = 1

    set_status(f"📥 Added to queue (total: {len(download_queue)})")
    url_entry.delete(0, "end")

add_btn = ctk.CTkButton(top, text="Add", width=120, command=add_to_queue)
add_btn.pack(side="left")

# Save folder
save_frame = ctk.CTkFrame(app)
save_frame.pack(padx=10, pady=6, fill="x")
ctk.CTkLabel(save_frame, text="Save to:", text_color="white").pack(side="left")
save_entry = ctk.CTkEntry(save_frame, width=480)
save_entry.pack(side="left", padx=8)
default_save = os.path.join(os.path.expanduser("~"), "Downloads")
save_entry.insert(0, default_save)

def _browse():
    folder = filedialog.askdirectory()
    if folder:
        save_entry.delete(0, "end")
        save_entry.insert(0, folder)

ctk.CTkButton(save_frame, text="Browse", width=100, command=_browse).pack(side="left")

# Mode (video/audio/auto)
mode_frame = ctk.CTkFrame(app)
mode_frame.pack(padx=10, pady=4, fill="x")
mode_var = ctk.StringVar(value="video")
ctk.CTkRadioButton(mode_frame, text="Video", variable=mode_var, value="video").pack(side="left", padx=8)
ctk.CTkRadioButton(mode_frame, text="Audio (MP3)", variable=mode_var, value="audio").pack(side="left", padx=8)
ctk.CTkRadioButton(mode_frame, text="Auto", variable=mode_var, value="auto").pack(side="left", padx=8)

# Middle: thumbnail + info
mid = ctk.CTkFrame(app)
mid.pack(padx=10, pady=6, fill="x")
thumb_label = ctk.CTkLabel(mid, text="No thumbnail", width=320, height=180, text_color="white")
thumb_label.pack(side="left", padx=6)
info_label = ctk.CTkLabel(mid, text="No video info yet", wraplength=340, justify="left", text_color="white")
info_label.pack(side="left", padx=6)

# Progress bar area (main window still shows summary)
progress_label = ctk.CTkLabel(app, text="Progress: 0%", text_color="white")
progress_label.pack(pady=6)
progress_bar = ctk.CTkProgressBar(app, width=780, progress_color="gray", mode="determinate")
progress_bar.set(0)
progress_bar.pack(pady=6)

# Controls
controls = ctk.CTkFrame(app)
controls.pack(padx=10, pady=6, fill="x")

download_btn = ctk.CTkButton(controls, text="Download", width=120)
pause_btn    = ctk.CTkButton(controls, text="Pause",    width=100)
resume_btn   = ctk.CTkButton(controls, text="Resume",   width=100)
cancel_btn   = ctk.CTkButton(controls, text="Cancel",   width=100, fg_color="red")
status_label = ctk.CTkLabel(controls, text="Ready", text_color="white")

download_btn.pack(side="left", padx=6)
pause_btn.pack(side="left", padx=6)
resume_btn.pack(side="left", padx=6)
cancel_btn.pack(side="left", padx=6)
status_label.pack(side="right")

def set_status(text, color=None):
    if color:
        try:
            status_label.configure(text_color=color)
        except Exception:
            pass
    status_label.configure(text=text)

# ---------------- Helpers ----------------
def seconds_to_time(sec):
    try:
        sec = int(sec)
    except Exception:
        return "0:00"
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"

def fetch_info_quiet(url):
    try:
        # allow extracting playlist metadata if present
        with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": "in_playlist"}) as ydl:
            info = ydl.extract_info(url, download=False)
        return info, None
    except Exception as e:
        return None, str(e)

def show_thumbnail_info(thumbnail, title, duration, idx_text):
    """عرض الصورة المصغرة في الواجهة الرئيسية؛ وإذا None -> إخفاء الصورة."""
    tk_img = None
    if thumbnail:
        try:
            r = requests.get(thumbnail, timeout=6)
            r.raise_for_status()
            img = Image.open(BytesIO(r.content)).convert("RGBA")
            img = img.resize((320, 180))
            tk_img = ImageTk.PhotoImage(img)
        except Exception:
            tk_img = None

    def _update():
        if tk_img:
            thumb_label.configure(image=tk_img, text="")
            thumb_label.image = tk_img
        else:
            thumb_label.configure(image=None, text="No thumbnail")
            thumb_label.image = None
        info_label.configure(text=f"{title}\nDuration: {seconds_to_time(duration)}\n{idx_text}")
        global color_activated
        color_activated = False
        try:
            progress_bar.configure(progress_color="no")
        except Exception:
            pass
        progress_bar.set(0)
        progress_label.configure(text="Progress: 0%")
    app.after(0, _update)

# ---------------- Progress popup helpers ----------------
def create_progress_popup():
    global progress_popup, popup_progress_bar, popup_progress_label
    if progress_popup and progress_popup.winfo_exists():
        return  # already exists
    progress_popup = ctk.CTkToplevel(app)
    progress_popup.title("Download Progress")
    progress_popup.geometry("520x120")
    progress_popup.resizable(False, False)
    popup_progress_label = ctk.CTkLabel(progress_popup, text="Starting...", text_color="white")
    popup_progress_label.pack(pady=(10,4))
    popup_progress_bar = ctk.CTkProgressBar(progress_popup, width=480, mode="determinate")
    popup_progress_bar.set(0)
    popup_progress_bar.pack(pady=(0,8))
    # prevent user from closing popup accidentally; user should Cancel from main UI
    try:
        progress_popup.protocol("WM_DELETE_WINDOW", lambda: None)
    except Exception:
        pass

def destroy_progress_popup():
    global progress_popup, popup_progress_bar, popup_progress_label
    try:
        if progress_popup and progress_popup.winfo_exists():
            progress_popup.destroy()
    except Exception:
        pass
    progress_popup = None
    popup_progress_bar = None
    popup_progress_label = None

# ---------------- Progress hook ----------------
def make_progress_hook():
    def progress_hook(d):
        global pause_flag, stop_flag, last_temp_filename
        filename = d.get("filename") or d.get("tmpfilename") or d.get("filepath")
        if filename:
            last_temp_filename = filename
        if stop_flag:
            raise Exception("CANCELLED_BY_USER")
        if pause_flag:
            raise Exception("PAUSED_BY_USER")
        status = d.get("status")
        if status == "downloading":
            percent = d.get("_percent_str", "0%").strip()
            speed = d.get("speed")
            eta = d.get("eta")
            try:
                p = float(percent.replace("%", "").strip()) / 100.0
            except Exception:
                p = 0.0

            def _upd():
                # update main progress bar
                global color_activated
                if p > 0 and not color_activated:
                    try:
                        progress_bar.configure(progress_color="green")
                    except Exception:
                        pass
                    color_activated = True
                progress_bar.set(max(0.0, min(1.0, p)))

                extra = ""
                if speed:
                    try:
                        if speed > 1024*1024:
                            sp = f"{speed/1024/1024:.2f} MB/s"
                        elif speed > 1024:
                            sp = f"{speed/1024:.2f} KB/s"
                        else:
                            sp = f"{speed:.0f} B/s"
                        extra += f" | {sp}"
                    except Exception:
                        pass
                if eta is not None:
                    try:
                        extra += f" | ETA: {seconds_to_time(int(eta))}"
                    except Exception:
                        pass
                progress_label.configure(text=f"{percent}{extra}")

                # update popup if exists
                if popup_progress_bar is not None and popup_progress_bar.winfo_exists():
                    try:
                        popup_progress_bar.set(max(0.0, min(1.0, p)))
                        popup_progress_label.configure(text=f"{percent}{extra}")
                    except Exception:
                        pass

            app.after(0, _upd)

        elif status == "finished":
            def _finish():
                try:
                    progress_bar.set(1.0)
                    progress_label.configure(text="100% | ETA: 0:00")
                    if popup_progress_bar is not None and popup_progress_bar.winfo_exists():
                        popup_progress_bar.set(1.0)
                        popup_progress_label.configure(text="100% | ETA: 0:00")
                except Exception:
                    pass
            app.after(0, _finish)
            time.sleep(0.12)
    return progress_hook

# ---------------- Worker ----------------
def worker(save_folder):
    """
    Worker loop:
    - peek at first item in queue (do NOT pop until download success)
    - if playlist item: pass playlist URL to yt_dlp with playlist enabled and outtmpl that creates folder
    - if video item: download single video
    - show thumbnail/info for the current item
    - handle pause/cancel
    """
    global is_downloading, pause_flag, stop_flag, last_temp_filename
    is_downloading = True
    pause_flag = False
    stop_flag = False

    create_progress_popup()
    set_status("Worker started...")

    while True:
        with queue_lock:
            if not download_queue:
                break
            item = download_queue[0]  # peek only

        url = item.get("url")
        item_type = item.get("type", "video")

        # fetch info for UI (title/thumbnail/duration)
        info, err = fetch_info_quiet(url)
        if info:
            # If playlist, show playlist title; else show video title
            if item_type == "playlist":
                title = info.get("title") or info.get("playlist_title") or "Playlist"
                thumb = ""
                dur = 0
            else:
                title = info.get("title", "Unknown")
                thumb = info.get("thumbnail", "")
                dur = info.get("duration", 0)
            show_thumbnail_info(thumb, title, dur, f"Remaining: {len(download_queue)}")
            set_status(f"Starting: {title}", "white")
            if popup_progress_label is not None:
                try:
                    popup_progress_label.configure(text=f"Starting: {title}")
                except Exception:
                    pass
        else:
            show_thumbnail_info("", url, 0, f"Remaining: {len(download_queue)}")
            set_status("Starting...", "white")

        # prepare ydl options depending on type and mode
        mode = mode_var.get()
        audio_only = (mode == "audio")
        ydl_opts = {
            "outtmpl": os.path.join(save_folder, "%(title)s.%(ext)s"),
            "continuedl": True,
            "noplaylist": True,   # default (overridden for playlist)
            "progress_hooks": [make_progress_hook()],
            "quiet": True,
        }
        if FFMPEG_PATH:
            ydl_opts["ffmpeg_location"] = FFMPEG_PATH

        if item_type == "playlist":
            # instruct yt_dlp to download the playlist as a whole into a folder
            ydl_opts["noplaylist"] = False
            ydl_opts["outtmpl"] = os.path.join(save_folder, "%(playlist_title)s", "%(playlist_index)s - %(title)s.%(ext)s")

        # format / postprocessors
        if audio_only:
            ydl_opts["format"] = "bestaudio/best"
            if FFMPEG_EXISTS:
                ydl_opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192"
                }]
        else:
            ydl_opts["format"] = "bestvideo+bestaudio/best" if FFMPEG_EXISTS else "best"

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # on success -> remove queue item
            with queue_lock:
                if download_queue and download_queue[0] == item:
                    download_queue.pop(0)

            # clear thumbnail/info
            def clear_thumb():
                thumb_label.configure(image=None, text="No thumbnail")
                thumb_label.image = None
                info_label.configure(text="No video info yet")
            app.after(0, clear_thumb)

            set_status("Finished.", "green")
            time.sleep(0.2)

        except Exception as e:
            msg = str(e)
            if "PAUSED_BY_USER" in msg:
                set_status("Paused", "orange")
                is_downloading = False
                destroy_progress_popup()
                return
            if "CANCELLED_BY_USER" in msg:
                set_status("Cancelled", "red")
                try:
                    if last_temp_filename and os.path.exists(last_temp_filename):
                        os.remove(last_temp_filename)
                except Exception:
                    pass
                is_downloading = False
                destroy_progress_popup()
                return
            # other errors
            set_status(f"Error: {msg}", "red")
            is_downloading = False
            destroy_progress_popup()
            return

    # finished all
    is_downloading = False
    set_status("All done ✅", "green")
    destroy_progress_popup()
    app.after(0, lambda: progress_bar.set(0))

# ---------------- Actions (start/pause/resume/cancel) ----------------
def start_downloads():
    global worker_thread, is_downloading, pause_flag, stop_flag
    if is_downloading:
        set_status("⚠️ Download in progress.")
        return
    save_folder = save_entry.get().strip() or default_save
    if not os.path.isdir(save_folder):
        try:
            os.makedirs(save_folder, exist_ok=True)
        except Exception as e:
            set_status(f"❌ Invalid folder: {e}")
            return
    with queue_lock:
        if not download_queue:
            set_status("❌ There are no items in the queue.")
            return
    pause_flag = False
    stop_flag = False
    worker_thread = threading.Thread(target=worker, args=(save_folder,), daemon=True)
    worker_thread.start()
    set_status("Worker started...", "white")

def pause_action():
    global pause_flag
    if not is_downloading:
        set_status("ℹ️ No active download.")
        return
    pause_flag = True
    set_status("⏸ Pausing...")

def resume_action():
    global pause_flag, worker_thread, is_downloading
    if is_downloading:
        set_status("ℹ️ Download already running.")
        return
    with queue_lock:
        if not download_queue:
            set_status("ℹ️ Queue is empty.")
            return
    pause_flag = False
    set_status("▶️ Resuming...")
    worker_thread = threading.Thread(target=worker, args=(save_entry.get().strip() or default_save,), daemon=True)
    worker_thread.start()

def cancel_action():
    global stop_flag
    if not is_downloading:
        set_status("ℹ️ No active download.")
        return
    stop_flag = True
    set_status("⛔ Cancelling...", "red")

download_btn.configure(command=start_downloads)
pause_btn.configure(command=pause_action)
resume_btn.configure(command=resume_action)
cancel_btn.configure(command=cancel_action)
# زر إعادة التهيئة (مسح القائمة)
def reset_queue_action():
    global download_queue
    with queue_lock:
        download_queue.clear()
    set_status("🗑️ Queue cleared (total: 0)", "green")
    # تفريغ الواجهة (thumbnail + info + progress)
    thumb_label.configure(image=None, text="No thumbnail")
    thumb_label.image = None
    info_label.configure(text="No video info yet")
    progress_bar.set(0)
    progress_label.configure(text="Progress: 0%")

reset_btn = ctk.CTkButton(controls, text="Remove All", width=120, fg_color="#065dac", command=reset_queue_action)
reset_btn.pack(side="left", padx=6)

# ---------------- Clipboard monitor (no popup) ----------------
def clipboard_worker():
    global last_clipboard
    while True:
        try:
            txt = pyperclip.paste()
        except Exception:
            txt = ""
        if isinstance(txt, str) and txt != last_clipboard:
            last_clipboard = txt
            if supported_url_re.search(txt):
                link = txt.strip()
                def _apply():
                    # only fill if user field empty (won't override user's manual text)
                    if not url_entry.get().strip():
                        url_entry.delete(0, "end")
                        url_entry.insert(0, link)
                    set_status("🔗 Link captured from clipboard.", "white")
                app.after(50, _apply)
        time.sleep(1.0)

threading.Thread(target=clipboard_worker, daemon=True).start()

# ---------------- Clean exit ----------------
def on_close():
    global stop_flag, pause_flag
    if is_downloading:
        # cancel quietly and close
        stop_flag = True
        pause_flag = False
        time.sleep(0.2)
    try:
        destroy_progress_popup()
    except Exception:
        pass
    app.destroy()

app.protocol("WM_DELETE_WINDOW", on_close)

# ---------------- Start UI loop ----------------
app.mainloop()
