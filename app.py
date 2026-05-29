import streamlit as st
import fitz
import re
import os
import io
import tempfile

# ── 설정 ────────────────────────────────────────────────────
BG = 0.11
SUFFIX = '_흑판'

st.set_page_config(
    page_title="PDF 흑판 변환기",
    page_icon="📄",
    layout="centered"
)

# ── 스타일 ───────────────────────────────────────────────────
st.markdown("""
<style>
    .main { background-color: #0e0e1a; }
    h1 { color: #ffffff; text-align: center; }
    .subtitle { color: #8888bb; text-align: center; font-size: 15px; margin-bottom: 2rem; }
    .stDownloadButton > button {
        background-color: #00aa44;
        color: white;
        font-size: 16px;
        font-weight: bold;
        border-radius: 8px;
        padding: 0.6rem 2rem;
        border: none;
        width: 100%;
    }
    .stDownloadButton > button:hover { background-color: #008833; }
</style>
""", unsafe_allow_html=True)


# ── 변환 함수 ────────────────────────────────────────────────

def _darken_graphics(text):
    # 밝은색(배경) → 어두운 BG, 어두운색(밑줄/선 등) → 흰색
    text = re.sub(
        r'\b(\d+(?:\.\d+)?)\s+g\b',
        lambda m: f'{BG} g' if float(m.group(1)) >= 0.5 else '1 g',
        text
    )
    text = re.sub(
        r'\b(\d+(?:\.\d+)?)\s+G\b',
        lambda m: f'{BG} G' if float(m.group(1)) >= 0.5 else '1 G',
        text
    )
    def _rg(mo):
        avg = (float(mo.group(1)) + float(mo.group(2)) + float(mo.group(3))) / 3
        return f'{BG} {BG} {BG} rg' if avg >= 0.5 else '1 1 1 rg'
    text = re.sub(
        r'\b(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+rg\b',
        _rg, text
    )
    def _RG(mo):
        avg = (float(mo.group(1)) + float(mo.group(2)) + float(mo.group(3))) / 3
        return f'{BG} {BG} {BG} RG' if avg >= 0.5 else '1 1 1 RG'
    text = re.sub(
        r'\b(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+RG\b',
        _RG, text
    )
    return text


def _whiten_text(inner):
    inner = re.sub(
        r'\b(\d+(?:\.\d+)?)\s+g\b',
        lambda m: '1 g' if float(m.group(1)) < 0.5 else m.group(0),
        inner
    )
    inner = re.sub(
        r'\b(\d+(?:\.\d+)?)\s+G\b',
        lambda m: '1 G' if float(m.group(1)) < 0.5 else m.group(0),
        inner
    )
    def _rg(mo):
        avg = (float(mo.group(1)) + float(mo.group(2)) + float(mo.group(3))) / 3
        return '1 1 1 rg' if avg < 0.5 else mo.group(0)
    inner = re.sub(
        r'\b(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+rg\b', _rg, inner)
    def _RG(mo):
        avg = (float(mo.group(1)) + float(mo.group(2)) + float(mo.group(3))) / 3
        return '1 1 1 RG' if avg < 0.5 else mo.group(0)
    inner = re.sub(
        r'\b(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+RG\b', _RG, inner)
    return inner


def _convert_stream(stream_bytes):
    try:
        content = stream_bytes.decode('latin-1')
    except Exception:
        return stream_bytes
    out = []
    pos = 0
    for m in re.finditer(r'\bBT\b(.*?)\bET\b', content, re.DOTALL):
        out.append(_darken_graphics(content[pos:m.start()]))
        inner = _whiten_text(m.group(1))
        out.append('q\n')
        out.append('BT\n1 g\n1 G\n')
        out.append(inner)
        out.append('\nET\nQ\n')
        pos = m.end()
    out.append(_darken_graphics(content[pos:]))
    return ''.join(out).encode('latin-1')


def _is_image_heavy(doc):
    sample = min(5, len(doc))
    img_pages = 0
    for i in range(sample):
        page = doc[i]
        xrefs = list(page.get_contents())
        if not xrefs:
            continue
        try:
            raw = b''
            for xref in xrefs:
                s = doc.xref_stream(xref)
                if s:
                    raw += s
            content = raw.decode('latin-1', errors='replace')
            has_bt = bool(re.search(r'\bBT\b', content))
            has_img = bool(re.search(r'/\w+\s+Do\b', content))
            if has_img and not has_bt:
                img_pages += 1
        except Exception:
            pass
    return img_pages >= sample // 2


def _smart_dark(img):
    import numpy as np
    from PIL import Image as _Image
    arr = __import__('numpy').array(img, dtype=__import__('numpy').int16)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    is_white = (r > 220) & (g > 220) & (b > 220)
    is_black = (r < 60) & (g < 60) & (b < 60)
    result = arr.astype(__import__('numpy').uint8).copy()
    result[is_white] = [28, 28, 28]
    result[is_black] = [255, 255, 255]
    return _Image.fromarray(result)


def convert_pdf_bytes(input_bytes, progress_bar=None):
    """PDF 바이트를 받아 변환된 PDF 바이트 반환"""
    try:
        doc = fitz.open(stream=input_bytes, filetype="pdf")
        if doc.is_encrypted:
            raise ValueError("암호화된 PDF는 변환할 수 없습니다.")
    except Exception:
        doc = fitz.open("pdf", input_bytes)

    # 이미지 기반 PDF 감지
    if _is_image_heavy(doc):
        doc.close()
        return _convert_image_mode(input_bytes, progress_bar)

    total = len(doc)
    for i, page in enumerate(doc):
        if progress_bar:
            progress_bar.progress((i + 1) / total, text=f"변환 중... {i+1}/{total} 페이지")

        w, h = page.rect.width, page.rect.height
        dark_bg = (f'q {BG} {BG} {BG} rg 0 0 {w:.2f} {h:.2f} re f Q\n').encode('latin-1')

        xrefs = list(page.get_contents())
        if not xrefs:
            continue

        raw = b''
        for xref in xrefs:
            try:
                s = doc.xref_stream(xref)
                if s:
                    raw += s + b'\n'
            except Exception:
                pass

        new_stream = dark_bg + _convert_stream(raw)
        try:
            doc.update_stream(xrefs[0], new_stream)
            if len(xrefs) > 1:
                try:
                    page.set_contents([xrefs[0]])
                except Exception:
                    pass
        except Exception:
            pass

    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True, clean=True)
    doc.close()
    return buf.getvalue()


def _convert_image_mode(input_bytes, progress_bar=None):
    from PIL import Image
    doc = fitz.open(stream=input_bytes, filetype="pdf")
    out = fitz.open()
    total = len(doc)
    for i, page in enumerate(doc):
        if progress_bar:
            progress_bar.progress((i + 1) / total, text=f"변환 중... {i+1}/{total} 페이지 (이미지 모드)")
        mat = fitz.Matrix(150 / 72, 150 / 72)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        img_dark = _smart_dark(img)
        new_page = out.new_page(width=page.rect.width, height=page.rect.height)
        buf = io.BytesIO()
        img_dark.save(buf, format='JPEG', quality=90)
        new_page.insert_image(new_page.rect, stream=buf.getvalue())
    doc.close()
    result = io.BytesIO()
    out.save(result, garbage=4, deflate=True)
    out.close()
    return result.getvalue()


# ── UI ───────────────────────────────────────────────────────

st.title("📄 PDF 흑판 변환기")
st.markdown('<p class="subtitle">흰 배경 → 검정 배경  ·  텍스트 흰색  ·  하이라이트 가능</p>', unsafe_allow_html=True)

uploaded_files = st.file_uploader(
    "PDF 파일을 여기에 올려주세요 (여러 개 가능)",
    type=["pdf"],
    accept_multiple_files=True,
    help="텍스트 레이어가 유지되어 드로우보드 등에서 하이라이트 가능합니다."
)

if uploaded_files:
    st.divider()
    for uploaded_file in uploaded_files:
        st.markdown(f"**{uploaded_file.name}**")
        col1, col2 = st.columns([3, 1])
        with col1:
            progress = st.progress(0, text="대기 중...")
        with col2:
            convert_btn = st.button("변환", key=f"btn_{uploaded_file.name}")

        if convert_btn:
            try:
                input_bytes = uploaded_file.read()
                result_bytes = convert_pdf_bytes(input_bytes, progress)
                progress.progress(1.0, text="✅ 완료!")

                out_name = os.path.splitext(uploaded_file.name)[0] + SUFFIX + '.pdf'
                st.download_button(
                    label=f"⬇️  {out_name} 다운로드",
                    data=result_bytes,
                    file_name=out_name,
                    mime="application/pdf",
                    key=f"dl_{uploaded_file.name}"
                )
            except Exception as e:
                progress.empty()
                st.error(f"변환 실패: {e}")

st.divider()
st.markdown(
    '<p style="color:#444466; text-align:center; font-size:12px;">'
    'PDF 텍스트 레이어 유지 · 드로우보드/굿노트 하이라이트 지원 · 이미지 교재 자동 감지</p>',
    unsafe_allow_html=True
)
