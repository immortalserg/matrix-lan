"""Readable email text without rendering remote HTML, scripts or tracking images."""
from html.parser import HTMLParser
import re

class PlainEmail(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.parts=[];self.hidden=0;self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','head'):self.hidden+=1
        if tag=='a' and not self.hidden:
            href=dict(attrs).get('href','') or ''
            self.links.append(href if href.startswith(('https://','http://')) and len(href)<=4096 and not any(ord(c)<32 for c in href) else '')
        if not self.hidden and tag in ('br','p','div','li','tr','h1','h2','h3','blockquote'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag=='a' and not self.hidden and self.links:
            href=self.links.pop()
            if href:self.parts.append(' ('+href+')')
        if tag in ('script','style','head') and self.hidden:self.hidden-=1
        if not self.hidden and tag in ('p','div','li','tr','h1','h2','h3','blockquote'):self.parts.append('\n')
    def handle_data(self,value):
        if not self.hidden:self.parts.append(value)

def plain_body(content):
    body=content.get('body');body=body if isinstance(body,str) else ''
    html=content.get('formatted_body')
    if content.get('format')=='org.matrix.custom.html' and isinstance(html,str) and len(html)<=256000:
        parser=PlainEmail()
        try:
            parser.feed(html)
            full=re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+','\n\n',''.join(parser.parts)).strip()
            if full:body=full
        except (ValueError,RecursionError):pass
    if len(body)>60000:return body[:59900]+'\n\n[Письмо слишком длинное. Полная версия доступна в Gmail.]'
    return body
