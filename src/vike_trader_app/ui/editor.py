"""CodeEditor — QPlainTextEdit with a line-number gutter and Python syntax highlighting."""

import keyword

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme


class _LineNumberArea(QtWidgets.QWidget):
    """Gutter sibling that paints block line numbers."""

    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        self._editor.line_number_area_paint_event(event)


class PythonHighlighter(QtGui.QSyntaxHighlighter):
    """Minimal Python syntax highlighter: keywords, strings, comments, def/class names."""

    def __init__(self, document: QtGui.QTextDocument):
        super().__init__(document)

        kw_fmt = QtGui.QTextCharFormat()
        kw_fmt.setForeground(QtGui.QColor(theme.BLUE))
        kw_fmt.setFontWeight(QtGui.QFont.Bold)

        str_fmt = QtGui.QTextCharFormat()
        str_fmt.setForeground(QtGui.QColor(theme.UP))

        comment_fmt = QtGui.QTextCharFormat()
        comment_fmt.setForeground(QtGui.QColor(theme.TEXT2))  # readable light-grey, not dim TEXT3
        comment_fmt.setFontItalic(True)

        name_fmt = QtGui.QTextCharFormat()
        name_fmt.setForeground(QtGui.QColor(theme.ACCENT))

        self._rules: list[tuple[QtCore.QRegularExpression, QtGui.QTextCharFormat]] = []

        # keywords
        kw_pattern = r"\b(" + "|".join(keyword.kwlist) + r")\b"
        self._rules.append((QtCore.QRegularExpression(kw_pattern), kw_fmt))

        # strings: double-quoted then single-quoted (simple single-line)
        self._rules.append((QtCore.QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"'), str_fmt))
        self._rules.append((QtCore.QRegularExpression(r"'[^'\\]*(\\.[^'\\]*)*'"), str_fmt))

        # comment
        self._rules.append((QtCore.QRegularExpression(r"#[^\n]*"), comment_fmt))

        # name after def / class
        self._rules.append((QtCore.QRegularExpression(r"(?<=\bdef\s)\w+"), name_fmt))
        self._rules.append((QtCore.QRegularExpression(r"(?<=\bclass\s)\w+"), name_fmt))

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        for rx, fmt in self._rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)


class CodeEditor(QtWidgets.QPlainTextEdit):
    """Plain-text code editor with a line-number gutter and Python syntax highlighting."""

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)

        self._gutter = _LineNumberArea(self)
        self._highlighter = PythonHighlighter(self.document())

        # monospace font
        font = QtGui.QFont("JetBrains Mono")
        font.setStyleHint(QtGui.QFont.Monospace)
        font.setFixedPitch(True)
        font.setPointSize(11)
        self.setFont(font)

        self.setTabStopDistance(
            QtGui.QFontMetricsF(self.font()).horizontalAdvance(" ") * 4
        )

        bg = theme.PANEL
        text = theme.TEXT
        border = theme.BORDER
        self.setStyleSheet(
            f"QPlainTextEdit{{background:{bg};color:{text};"
            f"border:1px solid {border};border-radius:6px;padding-left:2px;}}"
        )

        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter_scroll)
        self.cursorPositionChanged.connect(self._highlight_current_line)

        self._update_gutter_width(0)
        self._highlight_current_line()

    # --- public helpers ---

    def text(self) -> str:
        """Return the full editor content."""
        return self.toPlainText()

    def setText(self, s: str) -> None:
        """Replace entire editor content."""
        self.setPlainText(s)

    def line_number_area_width(self) -> int:
        """Width in pixels required to paint all line numbers."""
        digits = max(1, len(str(self.blockCount())))
        char_w = self.fontMetrics().horizontalAdvance("9")
        return 6 + char_w * digits

    # --- internal slots ---

    def _update_gutter_width(self, _count: int) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_gutter_scroll(self, rect: QtCore.QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width(0)

    def _highlight_current_line(self) -> None:
        selections: list[QtWidgets.QTextEdit.ExtraSelection] = []
        if not self.isReadOnly():
            sel = QtWidgets.QTextEdit.ExtraSelection()
            color = QtGui.QColor(theme.RAISE)
            sel.format.setBackground(color)
            sel.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
            sel.cursor = self.textCursor()
            sel.cursor.clearSelection()
            selections.append(sel)
        self.setExtraSelections(selections)

    # --- Qt overrides ---

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(
            QtCore.QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height())
        )

    def line_number_area_paint_event(self, event: QtGui.QPaintEvent) -> None:
        """Called by the gutter widget's paintEvent to draw line numbers."""
        painter = QtGui.QPainter(self._gutter)
        painter.fillRect(event.rect(), QtGui.QColor(theme.PANEL2))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.setPen(QtGui.QColor(theme.TEXT3))
                painter.drawText(
                    0, top,
                    self._gutter.width() - 3,
                    self.fontMetrics().height(),
                    QtCore.Qt.AlignRight,
                    str(block_number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1
