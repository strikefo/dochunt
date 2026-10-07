"""Isolated PDF worker: bounded memory, CPU, pages and extracted text; no OCR."""
import io
import json
import resource
import sys


def main():
    resource.setrlimit(resource.RLIMIT_CPU,(5,5))
    if sys.platform=='linux': resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
    from pypdf import PdfReader, overwrite_configuration
    overwrite_configuration(maximum_declared_stream_length=8_000_000, array_based_stream_maximum_output_length=8_000_000, zlib_maximum_output_length=8_000_000, lzw_maximum_output_length=8_000_000, run_length_maximum_output_length=8_000_000, image_maximum_buffer_size=8_000_000)
    body=sys.stdin.buffer.read(1_000_001)
    if len(body)>1_000_000: raise ValueError('PDF size cap')
    reader=PdfReader(io.BytesIO(body),strict=True)
    # Public PDFs may encrypt owner permissions while allowing empty-password
    # reading. Never try supplied passwords or a password search.
    if reader.is_encrypted and not reader.decrypt(''): raise ValueError('PDF requires reading password')
    if len(reader.pages)>80: raise ValueError('PDF unsupported')
    pages=[]; remaining=120000
    for number,page in enumerate(reader.pages,1):
        text=' '.join((page.extract_text() or '').split())[:min(20000,remaining)]
        if text: pages.append({'locator':f'PDF page {number}','text':text})
        remaining-=len(text)
        if remaining<=0: break
    if not pages: raise ValueError('PDF needs OCR')
    print(json.dumps(pages))


if __name__=='__main__': main()
