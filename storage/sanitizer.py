import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from bs4 import BeautifulSoup, Comment
import bleach


# ------- Configuration -------
ALLOWED_TAGS = [
    "a","abbr","b","blockquote","br","code","div","em","i","li","ol","p","pre",
    "span","strong","ul","table","thead","tbody","tr","td","th","img","h1","h2","h3","h4","h5","h6"
]
ALLOWED_ATTRIBUTES = {
    "*": ["class", "id", "title", "aria-label"],
    "a": ["href", "title", "rel", "target"],
    "img": ["src", "alt", "title", "width", "height"]
}
ALLOWED_PROTOCOLS = ["http", "https", "mailto", "tel"]  # do not include "javascript" or "data"
TRACKING_PARAMS = ["utm_source","utm_medium","utm_campaign","utm_term","utm_content",
                   "utm_name","fbclid","gclid","mc_cid","mc_eid","m","ref","_hsenc","_hsmi"]


# ------- Helpers -------
def strip_tracking_query_params(url: str) -> str:
    try:
        parsed = urlparse(url)
    except Exception:
        return url
    # keep only allowed schemes
    if parsed.scheme and parsed.scheme not in ALLOWED_PROTOCOLS:
        # if scheme is empty (relative link) allow; if not allowed, return empty (defang)
        return "" if parsed.scheme else url

    qs = parse_qsl(parsed.query, keep_blank_values=True)
    qs_filtered = [(k, v) for (k, v) in qs if not any(k.lower().startswith(tp) for tp in TRACKING_PARAMS)]
    new_q = urlencode(qs_filtered)
    new_parsed = parsed._replace(query=new_q)
    return urlunparse(new_parsed)


def is_safe_src_or_href(val: str) -> bool:
    if not val:
        return False
    val = val.strip()
    # reject javascript: and data: (unless you want to allow data:image/*)
    if re.match(r'(?i)^\s*(javascript:|vbscript:)', val):
        return False
    if val.lower().startswith("data:"):
        # reject data URIs to be conservative; allow only specific image data if desired
        return False
    return True


# ------- Main sanitizer -------
def sanitize_email_html(html: str, allow_images: bool = False, strip_styles: bool = True) -> str:
    """
    Sanitize an HTML email body.
    - remove dangerous tags/attributes
    - optionally remove images (set allow_images=False)
    - strip inline style attributes if strip_styles=True
    """
    # 1) Parse with BeautifulSoup and remove totally dangerous nodes
    soup = BeautifulSoup(html, "html5lib")

    # Remove comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    # Tags to fully remove (and their contents)
    for tagname in ["script", "style", "iframe", "object", "embed", "link", "form", "meta", "base", "head"]:
        for tag in soup.find_all(tagname):
            tag.decompose()

    # Remove tracking pixels (very small imgs)
    for img in soup.find_all("img"):
        try:
            w = img.get("width")
            h = img.get("height")
            src = img.get("src", "")
            # common trackers are 1x1 or tiny, or have "tracking" query strings / known domains
            if not allow_images:
                img.decompose()
                continue
            if (w and h and (w == "1" or h == "1")) or (src and re.search(r'pixel|track|beacon|tracker', src, re.I)):
                img.decompose()
                continue
            # sanitize src
            if not is_safe_src_or_href(src):
                img.decompose()
                continue
        except Exception:
            img.decompose()

    # Remove dangerous attributes from all tags (on*, style optionally)
    for tag in soup.find_all(True):
        attrs = dict(tag.attrs)
        for attr in list(attrs.keys()):
            if attr.lower().startswith("on"):  # onclick, onload, etc
                del tag.attrs[attr]
            elif attr.lower() in ("style",) and strip_styles:
                del tag.attrs[attr]
            elif attr.lower() in ("src", "href"):
                val = tag.attrs.get(attr, "")
                if not is_safe_src_or_href(val):
                    del tag.attrs[attr]
                else:
                    if attr.lower() == "href":
                        # strip tracking parameters and defang if scheme is disallowed
                        new = strip_tracking_query_params(val)
                        if not new:
                            del tag.attrs[attr]
                        else:
                            tag.attrs[attr] = new
                    else:
                        # src (images) - keep as-is (we already checked above)
                        tag.attrs[attr] = val
            else:
                # keep only whitelisted attributes per element (basic pass)
                pass

    cleaned_html_intermediate = str(soup)

    # 2) Use bleach to enforce a whitelist and ensure protocols are safe
    cleaner = bleach.Cleaner(tags=ALLOWED_TAGS,
                             attributes=ALLOWED_ATTRIBUTES,
                             protocols=ALLOWED_PROTOCOLS,
                             strip=True,
                             strip_comments=True)
    cleaned = cleaner.clean(cleaned_html_intermediate)

    # 3) Linkify any plain URLs, strip tracking params again, and add rel/target.
    #    bleach linkify callbacks receive attrs keyed by (namespace, name) tuples.
    cleaned = bleach.linkify(
        cleaned,
        callbacks=[_strip_tracking_callback, bleach.callbacks.nofollow, bleach.callbacks.target_blank],
        parse_email=False,
    )

    return cleaned


def _strip_tracking_callback(attrs, new=False):
    """bleach.linkify callback: re-strip tracking params from each anchor href."""
    href_key = (None, "href")
    if href_key in attrs:
        attrs[href_key] = strip_tracking_query_params(attrs[href_key])
    return attrs


def html_to_text(html):
    return " ".join(BeautifulSoup(html, "html.parser").stripped_strings)


def sanitize_email(raw_body: str, allow_images: bool = False) -> str:
    """Sanitize a raw email body to safe HTML. Returns the raw input unchanged if
    sanitization fails, so ingestion never loses content to a sanitizer error."""
    if not raw_body:
        return ""
    try:
        return sanitize_email_html(raw_body, allow_images=allow_images)
    except Exception:
        return raw_body


if __name__ == "__main__":
    sample_html = (
        '<p onclick="evil()">Hello '
        '<a href="https://example.com/article?utm_source=news&id=42">read more</a>'
        '</p><script>alert(1)</script>'
        '<img src="https://track.example.com/pixel.gif" width="1" height="1">'
    )
    safe = sanitize_email_html(sample_html, allow_images=False, strip_styles=True)
    print("SAFE HTML:", safe)
    print("AS TEXT  :", html_to_text(safe))

