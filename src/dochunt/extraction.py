"""Bounded extraction. Source text stays untrusted, separate from model prose."""
from html.parser import HTMLParser
from urllib.parse import urljoin
import json
import subprocess
import sys


def normalized(text): return ' '.join(text.split())


class Page(HTMLParser):
    VOID_TAGS={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    def __init__(self, url):
        super().__init__(convert_charrefs=True)
        self.url=url; self.text=[]; self.main_text=[]; self.links=[]; self.main_links=[]; self.title=[]
        self.hidden=0; self.chrome=0; self.main=0; self.in_title=0; self.stack=[]; self.positions={}
    def handle_starttag(self,tag,attrs):
        attributes=dict(attrs)
        hidden=int(tag in {'script','style','noscript'})
        main=int(tag=='main' or 'main' in (attributes.get('role') or '').casefold().split())
        chrome=int(tag=='nav' or (tag in {'header','footer'} and not self.main and not main))
        title=int(tag=='title')
        self.hidden+=hidden; self.chrome+=chrome; self.main+=main; self.in_title+=title
        if tag not in self.VOID_TAGS:
            self.positions.setdefault(tag,[]).append(len(self.stack))
            self.stack.append((tag,hidden,chrome,main,title))
        if tag=='a' and not self.hidden and not self.chrome:
            href=attributes.get('href','')
            if href:
                try: link=urljoin(self.url,href)
                except ValueError: return
                self.links.append(link)
                if self.main: self.main_links.append(link)
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        self.handle_endtag(tag)
    def handle_endtag(self,tag):
        # Unclosed nested markup ends with its enclosing element. Ignore stray
        # closing tags rather than letting them expose hidden/navigation text.
        positions=self.positions.get(tag)
        if not positions: return
        index=positions[-1]
        while len(self.stack)>index:
            closing,hidden,chrome,main,title=self.stack.pop()
            self.positions[closing].pop()
            if not self.positions[closing]: del self.positions[closing]
            self.hidden-=hidden; self.chrome-=chrome; self.main-=main; self.in_title-=title
    def handle_data(self,data):
        if not self.hidden:
            if self.in_title: self.title.append(data)
            elif not self.chrome:
                self.text.append(data)
                if self.main: self.main_text.append(data)


FEED_TYPES={'application/rss+xml','application/atom+xml'}


def extract_feed(body):
    """RSS or Atom items as one section each: 'published | title | link'. Entity and
    DOCTYPE declarations are refused, so no external or expanding entities are parsed."""
    import xml.etree.ElementTree as ET
    # Check the decoded text, so a UTF-16 body cannot hide a declaration.
    try: text=body.decode('utf-16') if body[:2] in (b'\xff\xfe',b'\xfe\xff') else body.decode('utf-8','replace')
    except UnicodeDecodeError: return None
    if '<!DOCTYPE' in text[:4000].upper() or '<!ENTITY' in text.upper():
        return None
    try: root=ET.fromstring(body)
    except ET.ParseError: return None
    strip=lambda tag: tag.rsplit('}',1)[-1]
    def child(node,*names):
        # Names in priority order: e.g. an Atom entry's <published> before <updated>.
        for name in names:
            for element in node:
                if strip(element.tag)==name:
                    return element
        return None
    sections=[]; links=[]
    for item in (e for e in root.iter() if strip(e.tag) in ('item','entry')):
        title=child(item,'title'); date=child(item,'pubDate','published','date','issued','updated')
        link=child(item,'link'); href=''
        if link is not None: href=(link.get('href') or link.text or '').strip()
        if not href: continue
        text=' | '.join((normalized(date.text or '') if date is not None else '', normalized(title.text or '') if title is not None else '', href))
        sections.append({'locator':'feed item','text':text}); links.append(href)
        if len(sections)>=200: break
    channel=child(root,'channel')
    title=child(root if strip(root.tag)=='feed' or channel is None else channel,'title')
    return {'title':normalized(title.text or '') if title is not None else 'Feed','sections':sections,'links':links,'format':'feed'}


def extract(body,mime,url,timeout):
    if mime=='application/pdf':
        try:
            result=subprocess.run([sys.executable,'-m','dochunt.pdf_extract'],input=body,capture_output=True,timeout=min(10,timeout),check=True)
            pages=json.loads(result.stdout)
            return {'title':'PDF document','sections':pages,'links':[],'format':'pdf'}
        except (subprocess.SubprocessError,ValueError):
            return None
    text=body.decode('utf-8',errors='replace')
    if mime=='text/html':
        page=Page(url); page.feed(text)
        main=normalized(' '.join(page.main_text))
        return {'title':normalized(' '.join(page.title)) or 'HTML document','sections':[{'locator':'HTML main content' if main else 'HTML text','text':(main or normalized(' '.join(page.text)))[:120000]}],'links':list(dict.fromkeys(page.main_links+page.links))[:100],'format':'html'}
    if mime in FEED_TYPES or (mime in {'text/xml','application/xml'} and (b'<rss' in body[:2000] or b'<feed' in body[:2000])):
        return extract_feed(body)
    if mime in {'text/plain','application/json','text/csv','application/csv'}:
        return {'title':'Public data document','sections':[{'locator':'Document text','text':normalized(text)[:120000]}],'links':[],'format':mime.split('/')[-1]}
    return None
