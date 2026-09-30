"""Settings, full screen: the phone's counterpart to the desktop's Options.

A Material settings list: sections under headings in the primary colour,
each setting its name and a line saying what it does, with a switch beside
it - a touch anywhere on it turns it over - or, for more than on and off,
the same segmented buttons the download page has. A change is saved at once,
and the downloader - in its own process - reads the settings again
(remote.py).
"""

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.scrollview import ScrollView

from grabbit import APP_VERSION

from . import theme
from .choose import OptionRow
from .motion import Entrance, ripple
from .widgets import FlatButton, Toggle, top_bar

AT_ONCE = [(count, str(count)) for count in range(1, 6)]
# How much a finished torrent gives back before it stops seeding, as a share of
# its size; 0 is aria2's word for no limit.
RATIOS = [(0.5, 'Half'), (1.0, '1×'), (2.0, '2×'), (0.0, 'Always')]


def friendly_folder(path: str) -> str:
    """/storage/emulated/0/Download/Grabbit, as a person would say it."""
    for root, name in (('/storage/emulated/0/', ''), ('/sdcard/', '')):
        if path.startswith(root):
            return name + path[len(root):]
    if path.startswith('/storage/') and path.count('/') >= 3:
        return 'SD card/' + path.split('/', 3)[3]
    return path


class SwitchLine(ButtonBehavior, BoxLayout):
    """A setting that is on or off: its name and what it does, the switch
    beside them. A touch anywhere on the line turns it over."""

    def __init__(self, title, note, active, on_change, **kwargs):
        super().__init__(size_hint_y=None, spacing=dp(16), padding=[0, dp(12)], **kwargs)
        words = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(2))
        words.bind(minimum_height=words.setter('height'))
        for text, size, color in ((title, theme.BODY_LARGE, theme.ON_SURFACE),
                                  (note, theme.BODY_MEDIUM, theme.ON_SURFACE_VARIANT)):
            if not text:
                continue
            label = Label(text=text, color=color, font_size=size, size_hint_y=None,
                          halign='left', valign='top')
            label.bind(width=lambda widget, width: setattr(widget, 'text_size', (width, None)),
                       texture_size=lambda widget, value: setattr(widget, 'height', value[1]))
            words.add_widget(label)
        words.bind(height=lambda _, height: setattr(self, 'height', height + dp(24)))
        self.switch = Toggle(active=active, on_change=on_change)
        self.add_widget(words)
        self.add_widget(self.switch)
        self.ripple = ripple(self, radius=dp(theme.MEDIUM), color=theme.ON_SURFACE)

    def on_release(self):
        self.switch.on_release()


class SettingsPage(Entrance, ModalView):
    """app is the GrabbitApp: its settings, its engine and its update checks."""

    def __init__(self, app, insets=(0, 0), **kwargs):
        super().__init__(size_hint=(1, 1), background='', background_color=theme.SURFACE,
                         overlay_color=(0, 0, 0, 0), auto_dismiss=True, **kwargs)
        self.app = app
        self.settings = app.settings
        self.answer = None              # the last update check's

        top, bottom = insets
        column = BoxLayout(orientation='vertical', spacing=dp(4),
                           padding=[dp(16), dp(10) + top, dp(16), dp(10) + bottom])
        header, _ = top_bar('Settings', self.dismiss, height=dp(56))
        column.add_widget(header)

        scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
        # Room on the right for the scroll bar, which is drawn over whatever
        # is there, so no choice has its edge under it.
        self.body = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(8),
                              padding=[0, 0, dp(9), dp(24)])
        self.body.bind(minimum_height=self.body.setter('height'))
        scroll.add_widget(self.body)
        column.add_widget(scroll)
        self.add_widget(column)
        self._build()

    # ------------------------------------------------------------ building
    def _build(self):
        body, settings = self.body, self.settings

        body.add_widget(self._section('Updates'))
        line = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(10))
        version = Label(text=f'Grabbit {APP_VERSION}', color=theme.ON_SURFACE,
                        font_size=theme.BODY_LARGE, halign='left', valign='middle')
        version.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        self.check_button = FlatButton(text='Check for updates', style='tonal', size_hint_x=None)
        self.check_button.bind(on_release=lambda *_: self.check_now())
        line.add_widget(version)
        line.add_widget(self.check_button)
        body.add_widget(line)
        self.update_note = self._note('')
        body.add_widget(self.update_note)
        self.get_slot = BoxLayout(size_hint_y=None, height=0)
        body.add_widget(self.get_slot)
        self.get_button = FlatButton(text='', style='filled')
        self.get_button.bind(on_release=lambda *_: self.get_update())
        self._switch('Check by itself',
                     'Whenever Grabbit opens, and twice a day while it is open.',
                     bool(settings.check_for_updates), 'check_for_updates')

        body.add_widget(self._section('Downloads'))
        self.at_once = self._setting(
            'Downloads at once', 'The rest wait their turn.', AT_ONCE,
            int(getattr(settings, 'android_downloads_at_once', 3) or 3),
            'android_downloads_at_once')
        body.add_widget(self._title('Download folder'))
        self.folder_note = self._note('')
        body.add_widget(self.folder_note)
        buttons = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        change = FlatButton(text='Change', style='tonal', size_hint_x=None)
        change.bind(on_release=lambda *_: self.choose_folder())
        self.default_button = FlatButton(text='Use the default', style='text', size_hint_x=None)
        self.default_button.bind(on_release=lambda *_: self.set_folder(''))
        buttons.add_widget(change)
        buttons.add_widget(self.default_button)
        buttons.add_widget(BoxLayout())
        body.add_widget(buttons)
        self._switch('Wi-Fi only',
                     'On mobile data, downloads and seeding wait, and carry on by '
                     'themselves once the phone is back on Wi-Fi.',
                     bool(getattr(settings, 'wifi_only', False)), 'wifi_only')

        body.add_widget(self._section('Torrents'))
        self._switch('Seed after downloading',
                     'Give back to the other people sharing a torrent once it is yours.',
                     bool(settings.seed_after_download), 'seed_after_download')
        self._setting('Stop seeding after giving back',
                      'How much of the torrent, measured against its size.',
                      RATIOS, float(settings.seed_ratio), 'seed_ratio')

        body.add_widget(self._section('Running in the background'))
        self.battery_note = self._note('')
        body.add_widget(self.battery_note)
        self.battery_slot = BoxLayout(size_hint_y=None, height=0)
        body.add_widget(self.battery_slot)
        self.battery_button = FlatButton(text='Allow', style='tonal', size_hint_x=None,
                                         pos_hint={'x': 0, 'center_y': 0.5})
        self.battery_button.bind(on_release=lambda *_: self.ask_for_battery())

        self.show_folder()
        self.show_battery()

    def _section(self, text):
        label = Label(text=text, color=theme.PRIMARY, font_size=theme.TITLE_SMALL,
                      size_hint_y=None, height=dp(44), halign='left', valign='bottom')
        label.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        return label

    @staticmethod
    def _title(text):
        label = Label(text=text, color=theme.ON_SURFACE, font_size=theme.BODY_LARGE,
                      size_hint_y=None, height=dp(28), halign='left', valign='bottom')
        label.bind(size=lambda widget, value: setattr(widget, 'text_size', value))
        return label

    @staticmethod
    def _note(text):
        # Nothing tall until there are words in it: an empty label never
        # measures itself, and would keep Kivy's own hundred pixels.
        label = Label(text=text, color=theme.ON_SURFACE_VARIANT, font_size=theme.BODY_MEDIUM,
                      size_hint_y=None, height=0, halign='left', valign='top')
        label.bind(width=lambda widget, width: setattr(widget, 'text_size', (width, None)),
                   texture_size=lambda widget, size: setattr(widget, 'height', size[1]))
        return label

    def _switch(self, title, note, active, name):
        """A setting that is on or off - saved as soon as it is turned over."""
        line = SwitchLine(title, note, active, lambda value: self.change(name, value))
        self.body.add_widget(line)
        return line

    def _setting(self, title, note, options, chosen, name):
        """A heading, what it does, and its choices - saved as soon as chosen."""
        self.body.add_widget(self._title(title))
        if note:
            self.body.add_widget(self._note(note))
        row = OptionRow(options, chosen, lambda value: self.change(name, value))
        self.body.add_widget(row)
        return row

    # -------------------------------------------------------------- saving
    def change(self, name, value):
        setattr(self.settings, name, value)
        self.settings.save()
        self.app.engine.settings_changed()

    # ------------------------------------------------------------- updates
    def check_now(self):
        self.update_note.text = 'Checking…'
        self.show_get(None)
        self.app.check_for_updates(manual=True, reply=self.answered)

    def answered(self, answer):
        self.answer = answer if answer.available else None
        if answer.available:
            self.update_note.text = f'Grabbit {answer.latest} is out.'
        elif answer.error:
            self.update_note.text = answer.error
        else:
            self.update_note.text = 'This is the newest version.'
        self.show_get(self.answer)

    def show_get(self, answer):
        self.get_slot.clear_widgets()
        if answer is None:
            self.get_slot.height = 0
            return
        self.get_button.text = f'Get Grabbit {answer.latest}'
        self.get_slot.add_widget(self.get_button)
        self.get_slot.height = dp(48)

    def get_update(self):
        if self.answer is not None:
            answer = self.answer
            self.dismiss()
            self.app.take_update(answer)

    # -------------------------------------------------------------- folder
    def show_folder(self):
        from grabbit_mobile import paths
        chosen = getattr(self.settings, 'android_download_dir', '') or ''
        actual = str(paths.chosen_downloads_dir(chosen))
        text = friendly_folder(actual)
        if chosen and actual != chosen:
            text += f'\nGrabbit cannot save to {friendly_folder(chosen)}, so it saves here.'
        self.folder_note.text = text
        self.default_button.disabled = not chosen

    def choose_folder(self):
        from grabbit_mobile.bootstrap import choose_folder
        if not choose_folder(lambda path: Clock.schedule_once(lambda *_: self.folder_chosen(path), 0)):
            self.folder_note.text = 'This phone cannot show a folder picker.'

    def folder_chosen(self, path):
        if not path:
            return                          # the picker was closed
        from grabbit_mobile import paths
        if str(paths.chosen_downloads_dir(path)) != path:
            # Android keeps apps out of most folders without all-files access.
            self.app.ask_for_storage()
        self.set_folder(path)

    def set_folder(self, path):
        self.change('android_download_dir', path)
        self.show_folder()

    # ------------------------------------------------------------- battery
    def show_battery(self):
        unrestricted = self.app.battery_unrestricted()
        self.battery_slot.clear_widgets()
        if unrestricted is None:
            self.battery_note.text = 'Only a phone has a say in this.'
            self.battery_slot.height = 0
        elif unrestricted:
            self.battery_note.text = ('Grabbit may run without battery limits, so Android '
                                      'lets it carry on and pick up again by itself.')
            self.battery_slot.height = 0
        else:
            self.battery_note.text = (
                'Downloads carry on with Grabbit closed and the screen off. With battery '
                'use unrestricted, Android also lets Grabbit pick them up again by itself '
                'if it ever has to stop it.')
            self.battery_slot.add_widget(self.battery_button)
            self.battery_slot.height = dp(48)

    def ask_for_battery(self):
        self.app.ask_for_battery()

    def on_resume(self):
        """Back from one of Android's screens: what it says may have changed."""
        self.show_battery()
        self.show_folder()
