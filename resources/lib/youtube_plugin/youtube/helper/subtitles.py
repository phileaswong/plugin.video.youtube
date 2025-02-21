# -*- coding: utf-8 -*-
"""
    Copyright (C) 2017-2021 plugin.video.youtube

    SPDX-License-Identifier: GPL-2.0-only
    See LICENSES/GPL-2.0-only for more information.
"""

from html import unescape
from urllib.parse import (parse_qs, urlsplit, urlunsplit, urlencode, urljoin)

import xbmcvfs
import requests
from ...kodion.utils import make_dirs


class Subtitles(object):
    LANG_NONE = 0
    LANG_PROMPT = 1
    LANG_CURR_FALLBACK = 2
    LANG_CURR = 3
    LANG_CURR_NO_ASR = 4

    BASE_PATH = 'special://temp/plugin.video.youtube/'
    SRT_FILE = ''.join([BASE_PATH, '%s.%s.srt'])

    def __init__(self, context, video_id, captions, headers=None):
        self.context = context
        self._verify = context.get_settings().verify_ssl()
        self.video_id = video_id
        self.language = (context.get_settings()
                         .get_string('youtube.language', 'en_US')
                         .replace('_', '-'))

        if not headers and 'headers' in captions:
            headers = captions['headers']
            headers.pop('Authorization', None)
            headers.pop('Content-Length', None)
            headers.pop('Content-Type', None)
        self.headers = headers

        ui = self.context.get_ui()
        self.prompt_override = (
            ui.get_home_window_property('prompt_for_subtitles') == video_id
        )
        ui.clear_home_window_property('prompt_for_subtitles')

        self.renderer = captions.get('playerCaptionsTracklistRenderer', {})
        self.caption_tracks = self.renderer.get('captionTracks', [])
        self.translation_langs = self.renderer.get('translationLanguages', [])

        try:
            default_audio = self.renderer.get('defaultAudioTrackIndex')
            default_audio = self.renderer.get('audioTracks')[default_audio]
        except (IndexError, TypeError):
            default_audio = None

        self.defaults = {
            'caption': {},
            'lang_code': 'und',
            'is_asr': False,
        }
        if default_audio is None:
            return

        default_caption = self.renderer.get(
            'defaultTranslationSourceTrackIndices', [None]
        )[0]

        if default_caption is None:
            default_caption = (
                default_audio.get('hasDefaultTrack')
                and default_audio.get('defaultCaptionTrackIndex')
            )

        if default_caption is None:
            try:
                default_caption = default_audio.get('captionTrackIndices')[0]
            except (IndexError, TypeError):
                default_caption = 0

        try:
            default_caption = self.caption_tracks[default_caption] or {}
        except IndexError:
            return

        self.defaults = {
            'caption': default_caption,
            'lang_code': default_caption.get('languageCode') or 'und',
            'is_asr': default_caption.get('kind') == 'asr',
        }

    def srt_filename(self, sub_language):
        return self.SRT_FILE % (self.video_id, sub_language)

    def _write_file(self, _file, contents):
        if not make_dirs(self.BASE_PATH):
            self.context.log_debug('Failed to create directories: %s' % self.BASE_PATH)
            return False
        self.context.log_debug('Writing subtitle file: %s' % _file)
        try:
            f = xbmcvfs.File(_file, 'w')
            f.write(contents)
            f.close()
            return True
        except:
            self.context.log_debug('File write failed for: %s' % _file)
            return False

    def _unescape(self, text):
        try:
            text = text.decode('utf8', 'ignore')
        except:
            self.context.log_debug('Subtitle unescape: failed to decode utf-8')
        try:
            text = unescape(text)
        except:
            self.context.log_debug('Subtitle unescape: failed to unescape text')
        return text

    def get_default_lang(self):
        return {
            'code': self.defaults['lang_code'],
            'is_asr': self.defaults['is_asr'],
        }

    def get_subtitles(self):
        if self.prompt_override:
            languages = self.LANG_PROMPT
        else:
            languages = self.context.get_settings().subtitle_languages()
        self.context.log_debug('Subtitle get_subtitles: for setting |%s|' % str(languages))
        if languages == self.LANG_NONE:
            return []
        if languages == self.LANG_CURR:
            list_of_subs = []
            list_of_subs.extend(self._get(self.language))
            list_of_subs.extend(self._get(language=self.language.split('-')[0]))
            return list(set(list_of_subs))
        if languages == self.LANG_CURR_NO_ASR:
            list_of_subs = []
            list_of_subs.extend(self._get(self.language, no_asr=True))
            list_of_subs.extend(self._get(language=self.language.split('-')[0], no_asr=True))
            return list(set(list_of_subs))
        if languages == self.LANG_PROMPT:
            return self._prompt()
        if languages == self.LANG_CURR_FALLBACK:
            list_of_subs = []
            list_of_subs.extend(self._get(language=self.language))
            list_of_subs.extend(self._get(language=self.language.split('-')[0]))
            list_of_subs.extend(self._get('en'))
            list_of_subs.extend(self._get('en-US'))
            list_of_subs.extend(self._get('en-GB'))
            return list(set(list_of_subs))
        self.context.log_debug('Unknown language_enum: %s for subtitles' % str(languages))
        return []

    def _get_all(self):
        list_of_subs = []
        for language in self.translation_langs:
            list_of_subs.extend(self._get(language=language.get('languageCode')))
        return list(set(list_of_subs))

    def _prompt(self):
        tracks = [(track.get('languageCode'), self._get_language_name(track)) for track in self.caption_tracks]
        translations = [(track.get('languageCode'), self._get_language_name(track)) for track in self.translation_langs]
        languages = tracks + translations
        if languages:
            choice = self.context.get_ui().on_select(self.context.localize(30560), [language_name for language, language_name in languages])
            if choice != -1:
                return self._get(lang_code=languages[choice][0], language=languages[choice][1])
            self.context.log_debug('Subtitle selection cancelled')
            return []
        self.context.log_debug('No subtitles found for prompt')
        return []

    def _get(self, lang_code='en', language=None, no_asr=False):
        fname = self.srt_filename(lang_code)
        if xbmcvfs.exists(fname):
            self.context.log_debug('Subtitle exists for: %s, filename: %s' % (lang_code, fname))
            return [fname]

        caption_track = None
        asr_track = None
        has_translation = False
        for track in self.caption_tracks:
            if lang_code == track.get('languageCode'):
                if language is not None:
                    if language == self._get_language_name(track):
                        caption_track = track
                        break
                elif no_asr and (track.get('kind') == 'asr'):
                    continue
                elif track.get('kind') == 'asr':
                    asr_track = track
                else:
                    caption_track = track

        if (caption_track is None) and (asr_track is not None):
            caption_track = asr_track

        for lang in self.translation_langs:
            if lang_code == lang.get('languageCode'):
                has_translation = True
                break

        if (lang_code != self.defaults['lang_code'] and not has_translation
                and caption_track is None):
            self.context.log_debug('No subtitles found for: %s' % lang_code)
            return []

        subtitle_url = None
        if caption_track is not None:
            base_url = self._normalize_url(caption_track.get('baseUrl'))
            has_translation = False
        elif has_translation:
            base_url = self._normalize_url(
                self.defaults['caption'].get('baseUrl')
            )
        else:
            base_url = None

        if base_url:
            subtitle_url = self._set_query_param(base_url,
                ('type', 'track'),
                ('fmt', 'vtt'),
                ('tlang', lang_code) if has_translation else (None, None),
            )

        if subtitle_url:
            self.context.log_debug('Subtitle url: %s' % subtitle_url)
            if not self.context.get_settings().subtitle_download():
                return [subtitle_url]
            result_auto = requests.get(subtitle_url, headers=self.headers,
                                       verify=self._verify, allow_redirects=True)

            if result_auto.text:
                self.context.log_debug('Subtitle found for: %s' % lang_code)
                self._write_file(fname, bytearray(self._unescape(result_auto.text), encoding='utf8', errors='ignore'))
                return [fname]
            self.context.log_debug('Failed to retrieve subtitles for: %s' % lang_code)
            return []
        self.context.log_debug('No subtitles found for: %s' % lang_code)
        return []

    def _get_language_name(self, track):
        key = 'languageName' if 'languageName' in track else 'name'
        lang_name = track.get(key, {}).get('simpleText')
        if not lang_name:
            track_name = track.get(key, {}).get('runs', [{}])
            if isinstance(track_name, list) and len(track_name) >= 1:
                lang_name = track_name[0].get('text')

        if lang_name:
            return self._recode_language_name(lang_name)

        return None

    @staticmethod
    def _recode_language_name(language_name):
        return language_name

    @staticmethod
    def _set_query_param(url, *pairs):
        if not url or not pairs:
            return url

        num_params = len(pairs)
        if not num_params:
            return url
        if not isinstance(pairs[0], (list, tuple)):
            if num_params >= 2:
                pairs = zip(*[iter(pairs)] * 2)
            else:
                return url

        scheme, netloc, path, query_string, fragment = urlsplit(url)
        query_params = parse_qs(query_string)

        for name, value in pairs:
            if name:
                query_params[name] = [value]

        new_query_string = urlencode(query_params, doseq=True)
        if isinstance(scheme, bytes):
            new_query_string = new_query_string.encode('utf-8')

        return urlunsplit((scheme, netloc, path, new_query_string, fragment))

    @staticmethod
    def _normalize_url(url):
        if not url:
            url = ''
        elif url.startswith(('http://', 'https://')):
            pass
        elif url.startswith('//'):
            url = urljoin('https:', url)
        elif url.startswith('/'):
            url = urljoin('https://www.youtube.com', url)
        return url
