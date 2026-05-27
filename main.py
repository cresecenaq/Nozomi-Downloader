import sys
import os
import re
import requests
import urllib.parse
from html import escape, unescape
from urllib.parse import urljoin, urlparse, unquote
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QTextEdit, QLabel, QListWidget, QStackedWidget, QSizePolicy, QListWidgetItem
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl, QTimer, QThread, pyqtSignal, Qt, QSize
from PyQt6.QtGui import QPainter, QColor, QBrush, QPainterPath, QPen, QIcon, QPixmap, QCursor, QClipboard



MEDIA_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".mp4", ".webm")
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Referer": "https://nozomi.la/",
}



def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


def get_colored_icon(icon_path, color_hex="#ffffff"):
    pixmap = QPixmap(resource_path(icon_path)) 
    if pixmap.isNull():
        return QIcon()
        
    painter = QPainter(pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QColor(color_hex))
    painter.end()
    
    return QIcon(pixmap)


def full_path_from_hash(value):
    value = str(value)
    if len(value) < 3:
        return value

    return f"{value[-1]}/{value[-3:-1]}/{value}"


def extract_media_links_from_post(session, post_url):
    match = re.search(r"/post/(\d+)", post_url)
    post_id = match.group(1) if match else "post"

    if post_id != "post":
        data_url = f"https://j.gold-usergeneratedcontent.net/post/{full_path_from_hash(post_id)}.json"
        response = session.get(data_url, timeout=20)
        response.raise_for_status()

        media_links = []

        data = response.json()
        for media in data.get("imageurls", []):
            dataid = media.get("dataid")
            media_type = media.get("type")

            if not dataid or not media_type:
                continue

            media_path = full_path_from_hash(dataid)
            if media.get("is_video"):
                media_links.append(f"https://v.gold-usergeneratedcontent.net/{media_path}.{media_type}")
            elif media_type == "gif":
                media_links.append(f"https://g.gold-usergeneratedcontent.net/{media_path}.gif")
            else:
                media_links.append(f"https://w.gold-usergeneratedcontent.net/{media_path}.webp")

        return media_links

    response = session.get(post_url, timeout=20)
    response.raise_for_status()
    return extract_media_links(post_url, response.text)


def extract_media_links(post_url, html):
    html = unescape(html).replace("\\/", "/")
    candidates = []

    attr_pattern = re.compile(
        r'''(?:src|href|content|data-src|data-original)\s*=\s*["']([^"']+)["']''',
        re.IGNORECASE,
    )
    raw_pattern = re.compile(
        r'''(?:https?:)?//[^\s"'<>]+?\.(?:jpg|jpeg|png|gif|webp|avif|mp4|webm)(?:\?[^"'<>\s]*)?''',
        re.IGNORECASE,
    )

    candidates.extend(match.group(1) for match in attr_pattern.finditer(html))
    candidates.extend(match.group(0) for match in raw_pattern.finditer(html))

    media_links = []
    seen = set()

    for candidate in candidates:
        media_url = urljoin(post_url, candidate.strip())
        if media_url.startswith("//"):
            media_url = "https:" + media_url

        if not urlparse(media_url).path.lower().endswith(MEDIA_EXTENSIONS):
            continue

        clean_url = media_url.split("#")[0]
        if clean_url not in seen:
            seen.add(clean_url)
            media_links.append(clean_url)

    full_size_links = [
        link for link in media_links
        if "thumbnail" not in link.lower()
        and "smalltn" not in link.lower()
        and "tn.nozomi" not in link.lower()
    ]

    return full_size_links or media_links


def media_filename(media_url, post_url, index):
    url_path = urlparse(media_url).path
    filename = os.path.basename(url_path)

    match = re.search(r"/post/(\d+)", post_url)
    post_id = match.group(1) if match else "post"

    if not os.path.splitext(filename)[1]:
        filename = f"{post_id}_{index}.bin"
    elif len(filename) < 8:
        ext = os.path.splitext(filename)[1]
        filename = f"{post_id}_{index}{ext}"
    else:
        filename = f"{post_id}_{filename}"

    filename = unquote(filename).strip()
    filename = re.sub(r'[<>:"/\\|?*]+', "_", filename)
    return filename[:180] or "downloaded_file"


def download_link_media(session, link, save_dir, index, total, log_callback, should_stop=None):
    if should_stop and should_stop():
        return 0

    if urlparse(link).path.lower().endswith(MEDIA_EXTENSIONS):
        media_links = [link]
    else:
        log_callback(f"[{index}/{total}] Ищю медиа в ссылке: {link}")
        media_links = extract_media_links_from_post(session, link)

    if not media_links:
        log_callback(f"[{index}/{total}] Медиа не найдено: {link}")
        return 0

    downloaded = 0
    log_callback(f"[{index}/{total}] Найдено медиа: {len(media_links)}")

    for media_index, media_url in enumerate(media_links, 1):
        if should_stop and should_stop():
            return downloaded

        file_name = media_filename(media_url, link, media_index)
        
        ext = os.path.splitext(file_name)[1].lower().replace('.', '')
        if not ext:
            ext = "other"
            
        target_dir = os.path.join(save_dir, ext)
        os.makedirs(target_dir, exist_ok=True)

        base, extt = os.path.splitext(file_name)
        path = os.path.join(target_dir, file_name)
        counter = 1

        while os.path.exists(path):
            path = os.path.join(target_dir, f"{base}_{counter}{extt}")
            counter += 1

        file_path = path

        response = session.get(media_url, stream=True, timeout=30)
        response.raise_for_status()

        with open(file_path, 'wb') as out_file:
            for chunk in response.iter_content(chunk_size=8192):
                if should_stop and should_stop():
                    out_file.close()
                    try:
                        os.remove(file_path)
                    except:
                        pass
                    return downloaded
                
                if chunk:
                    out_file.write(chunk)

        downloaded += 1
        log_callback(f"  └─ Медиа загружено: [{ext.upper()}]")

    return downloaded


def format_log_text(text):
    text = escape(text)
    if text.strip("- ") == "":
        return text

    url_pattern = re.compile(r"https?://\S+")
    number_pattern = re.compile(r"(?<![#\w])\d+(?![\w;])")
    parts = []
    last_pos = 0

    for match in url_pattern.finditer(text):
        plain_part = text[last_pos:match.start()]
        plain_part = number_pattern.sub(r'<span style="color: #4db8ff; font-weight: bold;">\g<0></span>', plain_part)
        parts.append(plain_part)
        url_text = match.group(0)
        formatted_url = f'<span style="color: #c778ff;">{url_text}</span>'
        parts.append(formatted_url)
        last_pos = match.end()

    tail = text[last_pos:]
    tail = number_pattern.sub(r'<span style="color: #4db8ff; font-weight: bold;">\g<0></span>', tail)
    parts.append(tail)
    text = "".join(parts)

    ext_pattern = re.compile(r"\[[A-Z0-9]+\]")
    text = ext_pattern.sub(r'<span style="color: #4db8ff; font-weight: bold;">\g<0></span>', text)

    highlights = {
        "ГОТОВО!": "#55ff55",
        "Все загрузки завершены!": "#55ff55",
        "Парсинг остановлен пользователем!": "#ff5555",
        "Скачивание остановлено пользователем!": "#ff5555",
        "Все ссылки удалены!": "#ff5555",
        "Ссылка удалена!": "#ff5555",
        "Ошибка!": "#ff5555"
    }

    for word, color in highlights.items():
        text = text.replace(word, f'<span style="color: {color}; font-weight: bold;">{word}</span>')

    return text


class DownloadThread(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, links):
        super().__init__()
        self.links = list(links)
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def should_stop(self):
        return self._stop_requested

    def run(self):
        links = [link.strip() for link in self.links if link.strip()]
        if not links:
            self.finished_signal.emit()
            return

        save_dir = "downloads"
        os.makedirs(save_dir, exist_ok=True)
        
        self.log_signal.emit(f"Найдено {len(links)} ссылок, начинаю загрузку в папку '{save_dir}'")
        self.log_signal.emit("-" * 30)

        session = requests.Session()
        session.headers.update(REQUEST_HEADERS)
        total_downloaded = 0

        for i, link in enumerate(links):
            if self.should_stop():
                self.log_signal.emit("-" * 30)
                self.log_signal.emit(f"Скачивание остановлено пользователем!")
                self.log_signal.emit(f"Скачано файлов: {total_downloaded}")
                self.finished_signal.emit()
                return

            try:
                downloaded = self.download_link_media(session, link, save_dir, i + 1, len(links))
                total_downloaded += downloaded
            except Exception as e:
                self.log_signal.emit(f"[{i+1}/{len(links)}] Ошибка! {e}")

        self.log_signal.emit("-" * 30)
        self.log_signal.emit(f"Все загрузки завершены! Скачано файлов: {total_downloaded}")
        self.finished_signal.emit()

    def download_link_media(self, session, link, save_dir, index, total):
        return download_link_media(
            session,
            link,
            save_dir,
            index,
            total,
            self.log_signal.emit,
            self.should_stop,
        )


class SingleDownloadThread(QThread):
    log_signal = pyqtSignal(str)
    
    def __init__(self, link):
        super().__init__()
        self.link = link
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def should_stop(self):
        return self._stop_requested
        
    def run(self):
        save_dir = "downloads"
        os.makedirs(save_dir, exist_ok=True)
        
        try:
            session = requests.Session()
            session.headers.update(REQUEST_HEADERS)
            
            downloaded = download_link_media(session, self.link, save_dir, 1, 1, self.log_signal.emit, self.should_stop)
            
            if self.should_stop():
                self.log_signal.emit("-" * 30)
                self.log_signal.emit("Скачивание остановлено пользователем!")
                self.log_signal.emit(f"Скачано файлов: {downloaded}")
            else:
                self.log_signal.emit(f"ГОТОВО! Скачано файлов: {downloaded}")
                
        except Exception as e:
            self.log_signal.emit(f"Ошибка! {e}")


class HoverListWidget(QListWidget):
    download_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    copy_requested = pyqtSignal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        
        self.setStyleSheet("""
            QListWidget { 
                background-color: #1e1e1e; color: #ffffff; font-family: Consolas; 
                font-size: 12px; border: 1px solid #444; border-radius: 4px; 
                padding: 5px; outline: none;
            }
            QListWidget::item { border-bottom: 1px solid #333; padding: 6px 4px; }
            QListWidget::item:hover { background-color: #2a2a2a; }
            QListWidget::item:selected { color: #ffffff; }
        """)

        self.hover_widget = QWidget(self.viewport())
        self.hover_widget.setStyleSheet("background: transparent;")
        
        layout = QHBoxLayout(self.hover_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        btn_style = """
            QPushButton { background: #333333; border: none; border-radius: 4px; }
            QPushButton:hover { background: #555555; }
        """

        self.btn_dl = QPushButton()
        self.btn_dl.setIcon(get_colored_icon("icons/download.svg", "#ffffff"))
        self.btn_dl.setIconSize(QSize(16, 16))
        self.btn_dl.setFixedSize(26, 26)
        self.btn_dl.setStyleSheet(btn_style)
        self.btn_dl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_dl.clicked.connect(self.on_dl_clicked)

        self.btn_del = QPushButton()
        self.btn_del.setIcon(get_colored_icon("icons/delete.svg", "#ffffff"))
        self.btn_del.setIconSize(QSize(16, 16))
        self.btn_del.setFixedSize(26, 26)
        self.btn_del.setStyleSheet(btn_style)
        self.btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_del.clicked.connect(self.on_del_clicked)

        layout.addWidget(self.btn_dl)
        layout.addWidget(self.btn_del)
        self.hover_widget.hide()

        self.current_link = ""

    def update_buttons_position(self, pos):
        item = self.itemAt(pos)
        if item:
            self.current_link = item.text()
            rect = self.visualItemRect(item)
            widget_width = self.hover_widget.sizeHint().width()
            widget_height = self.hover_widget.sizeHint().height()
            x = rect.right() - widget_width - 5
            y = rect.top() + (rect.height() - widget_height) // 2
            self.hover_widget.move(x, y)
            self.hover_widget.show()
        else:
            if not self.hover_widget.geometry().contains(pos):
                self.hover_widget.hide()
                self.current_link = ""

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self.update_buttons_position(event.pos())

    def on_dl_clicked(self):
        if self.current_link:
            self.download_requested.emit(self.current_link)

    def on_del_clicked(self):
        if self.current_link:
            link_to_delete = self.current_link
            self.current_link = ""
            
            self.delete_requested.emit(link_to_delete)
            
            QTimer.singleShot(10, self.refresh_hover_state)

    def refresh_hover_state(self):
        local_pos = self.viewport().mapFromGlobal(QCursor.pos())
        self.update_buttons_position(local_pos)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.hover_widget.hide()
        
    def wheelEvent(self, event):
        self.hover_widget.hide() 
        super().wheelEvent(event)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        item = self.itemAt(event.pos())
        if item:
            link = item.text()
            clipboard = QApplication.clipboard()
            clipboard.setText(link)
            
            popup_pos = self.mapTo(self.parentWidget(), event.pos())
            self.copy_requested.emit(link, popup_pos)


class CornerOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.radius = 4.0 

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        bg_color = QColor("#232323")
        path = QPainterPath()
        path.addRect(0, 0, float(self.width()), float(self.height()))
        
        clip_path = QPainterPath()
        clip_path.addRoundedRect(0.0, 0.0, float(self.width()), float(self.height()), self.radius, self.radius)
        
        path.addPath(clip_path)
        path.setFillRule(Qt.FillRule.OddEvenFill)
        
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(bg_color))
        painter.drawPath(path)
        
        painter.setPen(QPen(QColor("#444"), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(1, 1, self.width() - 2, self.height() - 2, self.radius, self.radius)


class RoundedBrowser(QWebEngineView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.overlay = CornerOverlay(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'overlay') and self.overlay:
            self.overlay.resize(event.size())
            self.overlay.raise_()


class NozomiDownloaderApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Nozomi Downloader")
        self.resize(1350, 800)

        self.setWindowIcon(QIcon(resource_path("icons/app.png")))

        self.setStyleSheet("""
            QMainWindow { background-color: #232323; }
            QLabel { color: #ffffff; font-size: 13px; font-family: Arial; font-weight: bold; margin: 0px; padding: 0px; }
            
            QLineEdit { 
                padding: 0 12px; font-size: 14px; border: 1px solid #444; 
                border-radius: 4px; background: #333; color: white;
                min-height: 45px; max-height: 45px; margin: 0px; 
            }
            
            QPushButton.ActionBtn { 
                background-color: #B52525; color: white; font-size: 14px; font-weight: bold; 
                border-radius: 4px; min-height: 45px; max-height: 45px; margin: 0px; border: none;
            }
            QPushButton.ActionBtn:hover { background-color: #911E1E; }
            QPushButton.ActionBtn:disabled { background-color: #555555; color: #888; }
            
            QPushButton.NavBtn { 
                font-size: 14px; font-weight: bold; border-radius: 4px; 
                min-height: 45px; max-height: 45px; margin: 0px; border: 1px solid transparent; 
            }
            
            QPushButton.ToolBtn { 
                background-color: #333; border-radius: 4px; border: 1px solid #555; 
                min-height: 45px; max-height: 45px; min-width: 45px; max-width: 45px; margin: 0px; 
            }
            QPushButton.ToolBtn:hover { background-color: #444; }
            QPushButton.ToolBtn:disabled { background-color: #222222; border: 1px solid #333; }
            
            QPushButton.ToggleBtn {
                background-color: #333; border-radius: 4px; border: 1px solid #555; 
                min-height: 45px; max-height: 45px; min-width: 45px; max-width: 45px; margin: 0px; 
            }
            QPushButton.ToggleBtn:hover { background-color: #444; }
            
            QTextEdit { 
                background-color: #1e1e1e; color: #ffffff; font-family: Consolas; 
                font-size: 12px; border: 1px solid #444; border-radius: 4px; 
                padding: 5px; margin: 0px;
                selection-background-color: #1e9bff;
                selection-color: #ffffff;
            }
        """)

        self.links = []
        self.active_single_downloads = {}

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        left_panel = QWidget()
        left_panel.setFixedWidth(400)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        nav_layout = QHBoxLayout()
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(5)
        nav_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter) 
        
        self.btn_nav_parse = QPushButton("Парсинг")
        self.btn_nav_parse.setProperty("class", "NavBtn")
        
        self.btn_nav_down = QPushButton("Скачивание")
        self.btn_nav_down.setProperty("class", "NavBtn")
        
        self.btn_nav_parse.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_nav_down.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        self.btn_nav_parse.clicked.connect(lambda: self.switch_tab(0))
        self.btn_nav_down.clicked.connect(lambda: self.switch_tab(1))
        
        nav_layout.addWidget(self.btn_nav_parse)
        nav_layout.addWidget(self.btn_nav_down)
        left_layout.addLayout(nav_layout)

        self.left_stack = QStackedWidget()
        self.left_stack.setContentsMargins(0, 0, 0, 0)
        
        self.page_parse = QWidget()
        self.init_parsing_ui()
        self.left_stack.addWidget(self.page_parse)
        
        self.page_down = QWidget()
        self.init_downloading_ui()
        self.left_stack.addWidget(self.page_down)
        
        left_layout.addWidget(self.left_stack)
        main_layout.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        right_top_container = QWidget()
        right_top_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        toolbar_layout = QHBoxLayout(right_top_container)
        toolbar_layout.setSpacing(5)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter) 

        self.btn_back = QPushButton()
        self.btn_back.setIcon(get_colored_icon("icons/back.svg", "#ffffff"))
        self.btn_back.setIconSize(QSize(20, 20))
        self.btn_back.setProperty("class", "ToolBtn")
        
        self.btn_forward = QPushButton()
        self.btn_forward.setIcon(get_colored_icon("icons/forward.svg", "#ffffff"))
        self.btn_forward.setIconSize(QSize(20, 20))
        self.btn_forward.setProperty("class", "ToolBtn")
        
        self.btn_refresh = QPushButton()
        self.btn_refresh.setIcon(get_colored_icon("icons/refresh.svg", "#ffffff"))
        self.btn_refresh.setIconSize(QSize(20, 20))
        self.btn_refresh.setProperty("class", "ToolBtn")
        
        self.browser_url_bar = QLineEdit()
        self.browser_url_bar.setPlaceholderText("Введите URL и нажмите Enter...")
        self.browser_url_bar.setMinimumWidth(200)
        self.browser_url_bar.returnPressed.connect(self.navigate_browser)
        
        self.btn_toggle_view = QPushButton() 
        self.btn_toggle_view.setIcon(get_colored_icon("icons/show.svg", "#ffffff"))
        self.btn_toggle_view.setIconSize(QSize(20, 20))
        self.btn_toggle_view.setProperty("class", "ToggleBtn")
        self.btn_toggle_view.clicked.connect(self.toggle_browser_view)

        toolbar_layout.addWidget(self.btn_back)
        toolbar_layout.addWidget(self.btn_forward)
        toolbar_layout.addWidget(self.btn_refresh)
        toolbar_layout.addWidget(self.browser_url_bar)
        toolbar_layout.addWidget(self.btn_toggle_view)

        right_layout.addWidget(right_top_container)

        self.browser = RoundedBrowser()
        self.browser.load(QUrl("https://nozomi.la/")) 
        right_layout.addWidget(self.browser)

        self.browser_spacer = QWidget()
        self.browser_spacer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.browser_spacer.hide()
        right_layout.addWidget(self.browser_spacer)

        self.btn_back.clicked.connect(self.browser.back)
        self.btn_forward.clicked.connect(self.browser.forward)
        self.btn_refresh.clicked.connect(self.browser.reload)
        self.browser.urlChanged.connect(self.update_url_bar)
        
        self.browser.urlChanged.connect(self.update_nav_buttons)
        self.browser.loadFinished.connect(self.update_nav_buttons)
        self.update_nav_buttons()

        main_layout.addWidget(right_panel)

        self.switch_tab(0)

        self.is_scraping = False
        self.current_page = 1
        self.base_url = ""
        self.poll_timer = QTimer()
        self.poll_timer.setInterval(1000)
        self.poll_timer.timeout.connect(self.check_dom_state)


    def update_nav_buttons(self, *args):
        self.btn_back.setEnabled(self.browser.history().canGoBack())
        self.btn_forward.setEnabled(self.browser.history().canGoForward())

    def navigate_browser(self):
        url = self.browser_url_bar.text().strip()
        if url:
            if not url.startswith("http"):
                url = "https://" + url
            self.browser.load(QUrl(url))

    def update_url_bar(self, qurl):
        self.browser_url_bar.setText(qurl.toString())

    def toggle_browser_view(self):
        is_visible = self.browser.isVisible()
        
        self.browser.setVisible(not is_visible)
        self.browser_spacer.setVisible(is_visible)
        
        if is_visible:
            self.btn_toggle_view.setIcon(get_colored_icon("icons/hide.svg", "#ffffff"))
            self.btn_toggle_view.setStyleSheet("""
                QPushButton { background-color: #B52525; border-radius: 4px; border: 1px solid #555; }
                QPushButton:hover { background-color: #911E1E; }
            """)
        else:
            self.btn_toggle_view.setIcon(get_colored_icon("icons/show.svg", "#ffffff"))
            self.btn_toggle_view.setStyleSheet("")

    def init_parsing_ui(self):
        from PyQt6.QtWidgets import QFrame
        layout = QVBoxLayout(self.page_parse)
        layout.setContentsMargins(0, 0, 0, 0) 
        layout.setSpacing(10)
        
        layout.addWidget(QLabel("Введите теги для поиска:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Например: kitagawa_marin feet -guro")
        self.url_input.setMinimumWidth(200)
        self.url_input.returnPressed.connect(self.toggle_scraping)
        layout.addWidget(self.url_input)

        self.start_btn = QPushButton("Начать парсинг")
        self.start_btn.setProperty("class", "ActionBtn")
        self.start_btn.clicked.connect(self.toggle_scraping)
        layout.addWidget(self.start_btn)

        layout.addWidget(QLabel("Логи парсинга:"))
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.log_area)

    def init_downloading_ui(self):
        from PyQt6.QtWidgets import QFrame
        layout = QVBoxLayout(self.page_down)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        
        bulk_actions_layout = QHBoxLayout()
        bulk_actions_layout.setContentsMargins(0, 0, 0, 0)
        bulk_actions_layout.setSpacing(5)

        self.download_btn = QPushButton("Скачать все")
        self.download_btn.setProperty("class", "ActionBtn")
        self.download_btn.clicked.connect(self.toggle_downloading)

        self.delete_all_btn = QPushButton("Удалить все")
        self.delete_all_btn.setProperty("class", "ActionBtn")
        self.delete_all_btn.clicked.connect(self.delete_all_links)

        layout.addWidget(QLabel("Список найденных ссылок:"))
        
        self.links_list = HoverListWidget()
        self.links_list.download_requested.connect(self.download_single_link)
        self.links_list.delete_requested.connect(self.delete_single_link)
        self.links_list.copy_requested.connect(self.show_copy_popup)
        layout.addWidget(self.links_list)

        self.copy_popup = QLabel("Ссылка скопирована", self.page_down)
        self.copy_popup.setStyleSheet("""
            QLabel {
                background-color: #111111;
                color: #ffffff;
                border: 1px solid #1e9bff;
                border-radius: 3px;
                padding: 3px 7px;
                font-size: 10px;
                font-family: Arial;
            }
        """)
        self.copy_popup.adjustSize()
        self.copy_popup.hide()
        self.copy_popup_timer = QTimer(self)
        self.copy_popup_timer.setSingleShot(True)
        self.copy_popup_timer.timeout.connect(self.copy_popup.hide)

        bulk_actions_layout.addWidget(self.download_btn)
        bulk_actions_layout.addWidget(self.delete_all_btn)
        layout.addLayout(bulk_actions_layout)

        layout.addWidget(QLabel("Логи скачивания:"))
        self.dl_log_area = QTextEdit()
        self.dl_log_area.setReadOnly(True)
        self.dl_log_area.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.dl_log_area)

    def switch_tab(self, index):
        self.left_stack.setCurrentIndex(index)
        
        active_style = """
            QPushButton { background-color: #B52525; color: white; border: 1px solid #B52525; border-radius: 4px; font-weight: bold; font-size: 14px; }
            QPushButton:hover { background-color: #911E1E; }
        """
        inactive_style = """
            QPushButton { background-color: #333333; color: #aaaaaa; border: 1px solid #444; border-radius: 4px; font-weight: bold; font-size: 14px; }
            QPushButton:hover { background-color: #444444; }
        """
        
        if index == 0:
            self.btn_nav_parse.setStyleSheet(active_style)
            self.btn_nav_down.setStyleSheet(inactive_style)
        else:
            self.btn_nav_parse.setStyleSheet(inactive_style)
            self.btn_nav_down.setStyleSheet(active_style)
            self.refresh_links_list()

    def refresh_links_list(self):
        self.links_list.clear()
        self.links_list.addItems(self.links)

    def delete_single_link(self, link_to_remove):
        if link_to_remove in self.links:
            self.links.remove(link_to_remove)
                    
        self.dl_log(f"Ссылка удалена! {link_to_remove.split('/')[-1]}")
        self.refresh_links_list()

    def delete_all_links(self):
        if hasattr(self, "dl_thread") and self.dl_thread.isRunning():
            self.dl_log("Сначала остановите скачивание!")
            return

        self.links.clear()
        self.links_list.clear()
        self.dl_log("Все ссылки удалены!")

    def download_single_link(self, link):
        if hasattr(self, "dl_thread") and self.dl_thread.isRunning():
            return
        if self.active_single_downloads:
            return
        self.dl_log_area.clear()
        self.download_btn.setText("Стоп")
        self.delete_all_btn.setEnabled(False)
        thread = SingleDownloadThread(link)
        self.active_single_downloads[link] = thread
        thread.log_signal.connect(self.dl_log)
        thread.finished.connect(lambda l=link: self.cleanup_single_download(l))
        thread.finished.connect(self.finish_downloading)
        thread.start()

    def cleanup_single_download(self, link):
        if link in self.active_single_downloads:
            del self.active_single_downloads[link]

    def show_copy_popup(self, link, pos):
        self.copy_popup.adjustSize()
        x = pos.x() + 10
        y = pos.y() - self.copy_popup.height() - 8
        x = min(max(0, x), self.page_down.width() - self.copy_popup.width())
        y = min(max(0, y), self.page_down.height() - self.copy_popup.height())
        self.copy_popup.move(x, y)
        self.copy_popup.raise_()
        self.copy_popup.show()
        self.copy_popup_timer.start(900)

    def dl_log(self, text):
        self.dl_log_area.append(format_log_text(text))
        scrollbar = self.dl_log_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def toggle_downloading(self):
        is_bulk_running = hasattr(self, "dl_thread") and self.dl_thread.isRunning()
        is_single_running = len(self.active_single_downloads) > 0
        
        if is_bulk_running or is_single_running:
            self.stop_downloading()
        else:
            self.start_downloading()

    def stop_downloading(self):
        self.download_btn.setEnabled(False)
        self.download_btn.setText("Останавливаю...")
        
        if hasattr(self, "dl_thread") and self.dl_thread.isRunning():
            self.dl_thread.stop()
            
        for thread in self.active_single_downloads.values():
            thread.stop()

    def finish_downloading(self):
        self.download_btn.setText("Скачать все")
        self.download_btn.setEnabled(True)
        self.delete_all_btn.setEnabled(True)

    def start_downloading(self):
        if not self.links:
            return
        if self.active_single_downloads:
            return
        self.download_btn.setText("Стоп")
        self.download_btn.setEnabled(True)
        self.delete_all_btn.setEnabled(False)
        self.dl_log_area.clear()
        self.dl_thread = DownloadThread(self.links)
        self.dl_thread.log_signal.connect(self.dl_log)
        self.dl_thread.finished_signal.connect(self.finish_downloading)
        self.dl_thread.start()

    def log(self, text):
        self.log_area.append(format_log_text(text))
        scrollbar = self.log_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def toggle_scraping(self):
        if self.is_scraping:
            self.stop_scraping()
        else:
            self.start_scraping()

    def stop_scraping(self):
        if not self.is_scraping:
            return

        self.is_scraping = False
        self.poll_timer.stop()
        self.browser.stop()
        self.start_btn.setText("Начать парсинг")
        self.start_btn.setEnabled(True)
        self.log("-" * 30)
        self.log("Парсинг остановлен пользователем!")
        self.log(f"Собрано {len(self.links)} ссылок")

    def start_scraping(self):
        tags = self.url_input.text().strip()
        if not tags: return

        self.start_btn.setText("Стоп")
        self.start_btn.setEnabled(True)
        self.log_area.clear()
        
        encoded_tags = urllib.parse.quote(tags)
        self.base_url = f"https://nozomi.la/search-Popular.html?q={encoded_tags}"
        self.current_page = 1
        self.is_scraping = True
        self.links.clear()
        self.refresh_links_list()

        self.log("Запуск процесса...")
        self.log(f"Сгенерирована ссылка: {self.base_url}")
        self.browser.load(QUrl(self.base_url))
        self.poll_timer.start()

    def check_dom_state(self):
        if not self.is_scraping: return

        js_script = """
        (function() {
            var thumbs = document.querySelectorAll('#thumbnail-divs .thumbnail-div a');
            if (thumbs.length === 0) return null; 

            var pages = 1;
            var pageEl = document.querySelector('.page-container ul li:last-child');
            if (pageEl) {
                var match = pageEl.innerText.match(/\\d+/);
                if (match) pages = parseInt(match[0]);
            }

            var links = [];
            thumbs.forEach(function(a) { links.push(a.href); });
            return { 'total_pages': pages, 'links': links };
        })();
        """
        self.browser.page().runJavaScript(js_script, self.process_js_result)

    def process_js_result(self, result):
        if not self.is_scraping or result is None: return

        self.poll_timer.stop()
        total_pages = result['total_pages']
        links = result['links']

        if self.current_page == 1:
            self.log(f"Всего страниц: {total_pages}")
            self.log("-" * 30)

        self.log(f"[{self.current_page}/{total_pages}]Спарсена страница {self.current_page}")
        
        added_links = 0
        for link in links:
            full_link = link if link.startswith("http") else f"https://nozomi.la{link}"
            clean_link = full_link.split("#")[0]
            if clean_link not in self.links:
                self.links.append(clean_link)
                self.links_list.addItem(clean_link)
                added_links += 1
        
        self.log(f"  └─ Собрано ссылок: {added_links}")

        if self.current_page < total_pages:
            self.current_page += 1
            next_js = f"""
            document.querySelector('#thumbnail-divs').innerHTML = '';
            window.location.hash = '#{self.current_page}';
            """
            self.browser.page().runJavaScript(next_js)
            self.poll_timer.start()
        else:
            self.is_scraping = False
            self.start_btn.setText("Начать парсинг")
            self.start_btn.setEnabled(True)
            self.log("-" * 30)
            self.log(f"\nГОТОВО! Всего спарсено {len(self.links)} ссылок, перейдите во вкладку 'Скачивание'")





if __name__ == "__main__":
    import ctypes
    try:
        # Создаем уникальный ID для программы
        myappid = 'nozomi.downloader.pro.1'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass
    # ==========================================

    app = QApplication(sys.argv)
    window = NozomiDownloaderApp()
    window.show()
    sys.exit(app.exec())