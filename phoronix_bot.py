#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4

# phoronix-bot -- Send Phoronix news to Weibo automatically
# Copyright (C) 2014 Tom Li.
# License: AGPL v3 or later.


import rpweibo
from utils import tweetlen

import queue
from xml.etree.ElementTree import ElementTree

import urllib.request
import re
import time

import threading
import signal

APP_KEY = "1011524190"
APP_SECRET = "1898b3f668368b9f4a6f7ac8ed4a918f"
REDIRECT_URL = "https://api.weibo.com/oauth2/default.html"

USERNAME = ""
PASSWORD = ""

PHORONIX_RSS = "http://www.phoronix.com/rss.php"

# send to stdout instead of Weibo
DEBUG = 1

# set the event to stop all threads
global_stop_event = threading.Event()


class Weibo():

    SINA_URL_RE = re.compile(r"(http://t.cn/[a-zA-Z0-9]{5,7})")

    def __init__(self, application):
        self.weibo = rpweibo.Weibo(application)
        self.authenticator = None

        self._weibo_queue = queue.Queue()
        sender = threading.Thread(target=self._sender)
        sender.daemon = True
        sender.start()

    def auth(self):
        self.weibo.auth(self.authenticator)

    def send(self, news):
        self._weibo_queue.put(news)

    def get_news_guid(self):

        def chunks(lst, n):
            for i in range(0, len(lst), n):
                yield lst[i:i + n]

        # get short urls from tweets
        short_urls = []

        tweets = self.weibo.get("statuses/user_timeline", trim_user=1, count=200)["statuses"]
        for tweet in tweets:
            if "-" not in tweet["text"]:
                continue

            match_url = self.SINA_URL_RE.findall(tweet["text"])
            try:
                url = match_url[0]
            except IndexError:
                continue

            short_urls.append(url)

        # because of the limitation of the API
        # split short urls list into chunks, 20 urls each
        short_urls_chunks = list(chunks(short_urls, 20))

        # revert short urls back to original urls
        original_urls_list = []

        for url_chunk in short_urls_chunks:
            original_urls = self.weibo.get("short_url/expand", url_short=url_chunk)["urls"]
            for url in original_urls:
                if "http://www.phoronix.com/vr.php?" not in url["url_long"]:
                    continue
                original_urls_list.append(url["url_long"])

        return original_urls_list

    @staticmethod
    def _generate_tweet(text, url):

        def cut_last(weibo):
            idx = 0
            cut = 0
            for i in weibo:
                idx += 1
                if i == " ":
                    cut = idx
            return weibo[0:cut - 1]

        free = len(url) - 1  # 1 for safe.
        while 140 - tweetlen(text) <= free:
            text = cut_last(text) + "..."
        return text + url

    def _sender(self):
        while not global_stop_event.is_set():
            news = self._weibo_queue.get(block=True)
            text = Weibo._generate_tweet("%s - %s" % (news.title, news.description),
                                         news.link)
            success = self._send(text)
            if success:
                news.status = News.SENT
            else:
                news.status = News.FAIL
            global_stop_event.wait(5)

    def _send(self, text):
        if DEBUG:
            print(text)
            return True

        failed_count = 0
        while not global_stop_event.is_set():
            try:
                self.weibo.post("statuses/update", status=text)
                return True
            except rpweibo.WeiboError:
                failed_count += 1
                if failed_count > 5:
                    return False
                global_stop_event.wait(10)


class News():

    NEW = 0
    SENT = 1
    FAIL = 2

    def __init__(self, website, title="", link="", description="", guid="", status=0):
            self.website = website
            self.title = title
            self.link = link
            self.description = description
            self.guid = guid
            self._status = status

    def load_from_xml(self, xml):
            self.title = xml.find("title").text
            self.link = xml.find("link").text
            self.description = xml.find("description").text
            self.guid = xml.find("guid").text
            self._status = self.NEW

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, value):
        if value in (self.NEW, self.SENT, self.FAIL):
            self._status = value
        else:
            raise RuntimeError("Invalid status for News()")
        self.website.dump()

    def __eq__(self, obj):
        try:
            result = bool(self.guid == obj.guid)
            return result
        except AttributeError:
            return False

    def __bool__(self):
        if not self.guid:
            return False
        else:
            return True


class Phoronix():

    def __init__(self):
        self._news = []

        try:
            news = self.load()
            if news:
                self._news = news
        except IOError:
            pass

        self.update()

    def _merge(self, new_news):

        def merge(old, new):
                overlap_begin = 0
                overlap_end = -1

                for idx, val in enumerate(old):
                    if val == new[overlap_begin]:
                        overlap_end = len(old) - 1 - idx

                delta = new[overlap_end + 1:]
                result = old.copy()
                result += delta

                return result

        self._news = merge(self._news, new_news)

    def _clean(self):
        sent = []

        for news in self._news:
            if news.status == News.SENT:
                sent.append(news)

        for sent_news in sent:
            self._news.remove(sent_news)

    def load(self):
        news_list = []

        with open("./news", "r") as news_dumpfile:
            for line in news_dumpfile:
                line = line.replace("\n", "")
                guid, status, title, link, description = line.split("\t")
                status = int(status)

                news = News(self, title=title, link=link, description=description, guid=guid, status=status)
                news_list.append(news)

        return news_list

    def dump(self):
        with open("./news", "w") as news_dumpfile:
            for news in self.news():
                news_dumpfile.write("%s\t%d\t%s\t%s\t%s\n" %
                                    (news.guid, news.status, news.title, news.link, news.description))

    def update(self):
        news_list = []
        while 1:
            try:
                rss_resource = urllib.request.urlopen(PHORONIX_RSS)
                news_list_raw = list(ElementTree(file=rss_resource).iter("item"))
                break
            except Exception:
                time.sleep(5)

        for news_xml in news_list_raw:
            news = News(self)
            news.load_from_xml(news_xml)
            news_list.insert(0, news)

        self._merge(news_list)
        # self._clean()
        self.dump()

    def news(self):
        for news in self._news:
            yield news


def exit_thread(signal, frame):
    global_stop_event.set()
    exit()


def main():
    app = rpweibo.Application(APP_KEY, APP_SECRET, REDIRECT_URL)
    weibo = Weibo(app)
    weibo.authenticator = rpweibo.UserPassAutheticator(USERNAME, PASSWORD)
    weibo.auth()

    sent_guid = weibo.get_news_guid()

    phoronix = Phoronix()
    for news in phoronix.news():
        if news.guid in sent_guid:
            news.status = News.SENT

    while 1:
        phoronix.update()

        need_to_send_news = []
        for news in phoronix.news():
            if news.status == News.SENT:
                continue
            need_to_send_news.append(news)

        delay = 0
        if len(need_to_send_news) > 3:
            # do not flood
            delay = 600

        for news in need_to_send_news:
            weibo.send(news)
            time.sleep(delay)

        time.sleep(120)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, exit_thread)
    main()
