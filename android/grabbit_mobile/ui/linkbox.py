"""The box a link is pasted into: Android's own text field, laid over the app.

Kivy draws its text boxes itself, and with them its own long-press bubble -
nothing like the menu every other app on the phone shows. So on a phone this
box is a real Android EditText, placed over the space the layout keeps for it:
holding it brings up Android's own Cut, Copy, Paste and Select all, with its
selection handles and the keyboard's clipboard - the stock behaviour, rather
than an imitation of it.

Android draws its views above the surface Kivy draws on, so the field is
hidden while anything opens over the main screen (a page, a sheet, a dialog),
and shown again once it closes. And the keyboard's focus goes back to Kivy's
surface whenever the field is done with: a Back press that reached Android
while the field held focus would close the whole app, not the page open in it.
Off a phone - the desktop preview - a Kivy TextInput stands in, so the layout
and the checks run the same way there.
"""

import logging

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Line, RoundedRectangle
from kivy.metrics import dp
from kivy.properties import StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.modalview import ModalView
from kivy.uix.textinput import TextInput

from . import theme

log = logging.getLogger(__name__)


def _android():
    """The pieces needed for a native field, or None off a phone."""
    try:
        from android.runnable import run_on_ui_thread
        from jnius import PythonJavaClass, autoclass, java_method
    except ImportError:
        return None
    return run_on_ui_thread, PythonJavaClass, autoclass, java_method


class LinkBox(BoxLayout):
    """`text` follows what is in the box; on_submit is the keyboard's Go."""

    text = StringProperty('')
    __events__ = ('on_submit',)

    def __init__(self, hint='Paste a link', **kwargs):
        super().__init__(**kwargs)
        self.hint = hint
        with self.canvas.before:
            Color(*theme.BASE)
            self._fill = RoundedRectangle(radius=[dp(8)])
            Color(*theme.BORDER)
            self._line = Line(width=1.0)
        self.bind(pos=self._redraw, size=self._redraw)
        pieces = _android()
        self.native = _NativeField(self, *pieces) if pieces else None
        if self.native is None:
            self._stand_in()

    def on_submit(self):
        pass

    def _redraw(self, *_):
        self._fill.pos = self.pos
        self._fill.size = self.size
        self._line.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(8))

    def _stand_in(self):
        field = TextInput(hint_text=self.hint, multiline=False, background_normal='',
                          background_active='', background_color=theme.TRANSPARENT,
                          foreground_color=theme.TEXT, hint_text_color=theme.DIM,
                          cursor_color=theme.BLUE, font_size=dp(14),
                          padding=[dp(10), dp(12)])
        field.bind(text=lambda _, value: setattr(self, 'text', value),
                   on_text_validate=lambda *_: self.dispatch('on_submit'))
        self.bind(text=lambda _, value: setattr(field, 'text', value)
                  if field.text != value else None)
        self.stand_in = field
        self.add_widget(field)

    def set_text(self, value: str):
        self.text = value
        if self.native is not None:
            self.native.set_text(value)

    def unfocus(self):
        """Put the keyboard away, as a tap on Download should."""
        if self.native is not None:
            self.native.unfocus()
        else:
            self.stand_in.focus = False


def _chars(autoclass, text: str):
    """Text as Android's views take it. pyjnius turns a Python str into a
    Java String, but not into the CharSequence setText and setHint ask for."""
    from jnius import cast
    return cast('java.lang.CharSequence', autoclass('java.lang.String')(text))


class _NativeField:
    """The EditText itself, and keeping it where the layout wants it."""

    def __init__(self, box, run_on_ui_thread, PythonJavaClass, autoclass, java_method):
        self.box = box
        self.view = None
        self.visible = True
        self._placed = None
        self._ui = run_on_ui_thread
        self._autoclass = autoclass

        class Watcher(PythonJavaClass):
            __javainterfaces__ = ['android/text/TextWatcher']

            @java_method('(Ljava/lang/CharSequence;III)V')
            def beforeTextChanged(self, text, start, count, after):
                pass

            @java_method('(Ljava/lang/CharSequence;III)V')
            def onTextChanged(self, text, start, before, count):
                pass

            @java_method('(Landroid/text/Editable;)V')
            def afterTextChanged(self, editable):
                value = editable.toString()
                Clock.schedule_once(lambda _: box.text != value and setattr(box, 'text', value))

        class Actions(PythonJavaClass):
            __javainterfaces__ = ['android/widget/TextView$OnEditorActionListener']

            @java_method('(Landroid/widget/TextView;ILandroid/view/KeyEvent;)Z')
            def onEditorAction(self, view, action, event):
                # The keyboard's Go comes once, with no event; a real Enter key
                # comes as a press and a release - act on the press alone.
                if event is None or event.getAction() == 0:
                    Clock.schedule_once(lambda _: box.dispatch('on_submit'))
                return True

        field = self

        class Keys(PythonJavaClass):
            __javainterfaces__ = ['android/view/View$OnKeyListener']

            @java_method('(Landroid/view/View;ILandroid/view/KeyEvent;)Z')
            def onKey(self, view, code, event):
                # Back with no keyboard left to close is Kivy's to handle, as
                # it is when the box is not in use - never Android's, which
                # would finish the activity.
                if code != 4:                                  # KEYCODE_BACK
                    return False
                surface = field._surface()
                view.clearFocus()
                if surface is not None:
                    surface.requestFocus()
                    surface.dispatchKeyEvent(event)
                return True

        # Held here: a listener Java still calls must not be collected.
        self._watcher, self._actions, self._keys = Watcher(), Actions(), Keys()
        self._create()
        self._place_later = Clock.create_trigger(lambda _: self._place(), 0)
        box.bind(pos=self._place_later, size=self._place_later)
        Window.bind(size=self._place_later)
        Clock.schedule_interval(lambda _: self._follow_modals(), 0.15)

    def _create(self):
        autoclass = self._autoclass
        box = self.box

        @self._ui
        def create():
            try:
                Activity = autoclass('org.kivy.android.PythonActivity')
                activity = Activity.mActivity
                Color = autoclass('android.graphics.Color')
                EditorInfo = autoclass('android.view.inputmethod.EditorInfo')
                InputType = autoclass('android.text.InputType')
                # The device's own dark theme, so the menu, the handles and the
                # cursor look exactly as they do in any other app here.
                themed = autoclass('android.view.ContextThemeWrapper')(
                    activity, autoclass('android.R$style').Theme_DeviceDefault)
                edit = autoclass('android.widget.EditText')(themed)
                edit.setSingleLine(True)
                edit.setHint(_chars(autoclass, box.hint))
                edit.setTextColor(Color.parseColor(theme.PALETTE['text']))
                edit.setHintTextColor(Color.parseColor(theme.PALETTE['dim']))
                edit.setBackgroundColor(Color.TRANSPARENT)      # Kivy draws the box
                edit.setTextSize(1, 14.0)                        # 14dp, as the rest of the app
                edit.setPadding(int(dp(10)), 0, int(dp(10)), 0)
                edit.setGravity(autoclass('android.view.Gravity').CENTER_VERTICAL)
                edit.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI)
                edit.setImeOptions(EditorInfo.IME_ACTION_GO | EditorInfo.IME_FLAG_NO_EXTRACT_UI)
                edit.addTextChangedListener(self._watcher)
                edit.setOnEditorActionListener(self._actions)
                edit.setOnKeyListener(self._keys)
                Activity.getLayout().addView(edit, self._params())
                self.view = edit
            except Exception:
                log.exception('could not make the link box')
        create()

    def _params(self):
        """Where the box is, in the layout's pixels: Kivy measures from the
        bottom of the surface, Android from its top."""
        box = self.box
        x, y = box.to_window(box.x, box.y)
        params = self._autoclass('android.widget.RelativeLayout$LayoutParams')(
            max(1, int(box.width)), max(1, int(box.height)))
        params.leftMargin = int(x)
        params.topMargin = int(Window.height - (y + box.height))
        return params

    def _place(self):
        if self.view is None:
            return
        box = self.box
        self._placed = (*box.to_window(box.x, box.y), box.width, box.height, Window.height)
        params = self._params()
        view = self.view

        @self._ui
        def place():
            view.setLayoutParams(params)
        place()

    def _follow_modals(self):
        """Out of the way while something covers the main screen - and where
        the layout last put the box, which may have moved before Android had
        made the field to move."""
        box = self.box
        if self.view is not None and self._placed != (*box.to_window(box.x, box.y), box.width,
                                                       box.height, Window.height):
            self._place()
        covered = any(isinstance(child, ModalView) for child in Window.children)
        if covered == (not self.visible) or self.view is None:
            return
        self.visible = not covered
        view = self.view
        View = self._autoclass('android.view.View')

        @self._ui
        def show():
            view.setVisibility(View.GONE if covered else View.VISIBLE)
            if covered:
                # Hidden first, so the focus it gives up cannot land back on it.
                self._drop_keyboard(view)
        show()

    def _surface(self):
        """The view SDL draws Kivy on, which should hold focus by default."""
        try:
            return self._autoclass('org.kivy.android.PythonActivity').getSurface()
        except Exception:
            return None

    def _drop_keyboard(self, view):
        """On Android's thread: no keyboard, and focus back with Kivy."""
        try:
            from jnius import cast
            service = self._autoclass('org.kivy.android.PythonActivity').mActivity \
                .getSystemService('input_method')
            cast('android.view.inputmethod.InputMethodManager', service) \
                .hideSoftInputFromWindow(view.getWindowToken(), 0)
            view.clearFocus()
            surface = self._surface()
            if surface is not None:
                surface.requestFocus()
        except Exception:
            log.exception('could not put the keyboard away')

    def set_text(self, value: str):
        view = self.view
        if view is None:
            return

        text = _chars(self._autoclass, value)

        @self._ui
        def write():
            view.setText(text)
        write()

    def unfocus(self):
        view = self.view
        if view is None:
            return

        @self._ui
        def drop():
            self._drop_keyboard(view)
        drop()
