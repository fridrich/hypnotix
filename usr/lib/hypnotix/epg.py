import datetime
import gzip
import os
import time
import urllib.request
import xml.etree.ElementTree as ET

from common import EPG_PATH, async_function, slugify


def parse_xmltv_time(s: str) -> int:
    """Converts XMLTV timestamp ('20260807143000 +0200') to a Unix epoch integer."""
    if not s:
        return 0
    parts = s.strip().split()
    dt_str = parts[0][:14]
    try:
        dt = datetime.datetime.strptime(dt_str, "%Y%m%d%H%M%S")
        if len(parts) > 1:
            tz_str = parts[1]
            sign = 1 if tz_str[0] == "+" else -1
            hours = int(tz_str[1:3])
            mins = int(tz_str[3:5])
            offset = datetime.timedelta(hours=hours, minutes=mins)
            if sign == 1:
                dt -= offset
            else:
                dt += offset
        return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())
    except Exception:
        return 0


class IPTVSimpleMatcher:
    """Implements Kodi pvr.iptvsimple's 3-pass channel matching algorithm."""

    def __init__(self, xmltv_channels):
        self.xml_by_id = {}
        self.xml_by_display_name = {}
        self.xml_by_underscore_name = {}

        for ch in xmltv_channels:
            cid = ch["id"]
            self.xml_by_id[cid.lower()] = cid

            for name in ch.get("display_names", []):
                clean_name = name.strip().lower()
                self.xml_by_display_name[clean_name] = cid
                self.xml_by_underscore_name[clean_name.replace(" ", "_")] = cid

    def match_channel(self, tvg_id: str, tvg_name: str, m3u_title: str) -> str:
        # Pass 1: tvg-id == XMLTV id
        if tvg_id:
            tid = tvg_id.strip().lower()
            if tid in self.xml_by_id:
                return self.xml_by_id[tid]

        # Pass 2: tvg-name == XMLTV display-name (or space-to-_)
        if tvg_name:
            tname = tvg_name.strip().lower()
            if tname in self.xml_by_display_name:
                return self.xml_by_display_name[tname]
            if tname in self.xml_by_underscore_name:
                return self.xml_by_underscore_name[tname]

        # Pass 3: M3U stream title == XMLTV display-name
        if m3u_title:
            title = m3u_title.strip().lower()
            if title in self.xml_by_display_name:
                return self.xml_by_display_name[title]

        return None


class XMLTVParser:
    """Stream-parses XMLTV data without loading the entire DOM into RAM."""

    @staticmethod
    def _open_source(file_path: str):
        if file_path.endswith(".gz"):
            return gzip.open(file_path, "rb")
        return open(file_path, "rb")

    @classmethod
    def parse_guide(cls, file_path: str):
        channels = []
        programmes = {}  # { xmltv_id: [ {"start": ts, "stop": ts, "title": "...", "desc": "..."}, ... ] }

        try:
            with cls._open_source(file_path) as f:
                for event, elem in ET.iterparse(f, events=("end",)):
                    if elem.tag == "channel":
                        cid = elem.attrib.get("id")
                        if cid and cid.strip():
                            cid = cid.strip()
                            names = [e.text for e in elem.findall("display-name") if e.text]
                            channels.append({"id": cid, "display_names": names})
                        elem.clear()

                    elif elem.tag == "programme":
                        cid = elem.attrib.get("channel")
                        start = parse_xmltv_time(elem.attrib.get("start"))
                        stop = parse_xmltv_time(elem.attrib.get("stop"))

                        title_elem = elem.find("title")
                        title = title_elem.text if title_elem is not None else ""

                        desc_elem = elem.find("desc")
                        desc = desc_elem.text if desc_elem is not None else ""

                        if cid and start and stop and title:
                            cid = cid.strip()
                            if cid not in programmes:
                                programmes[cid] = []
                            programmes[cid].append({
                                "start": start,
                                "stop": stop,
                                "title": title,
                                "desc": desc
                            })
                        elem.clear()
        except Exception as e:
            print(f"[EPG] Parse error ({file_path}): {e}")

        return channels, programmes


class EPGManager:
    """Handles fetching, caching, matching, and querying EPG data."""

    def __init__(self, user_agent="Hypnotix"):
        self.user_agent = user_agent
        self.programmes = {}  # { xmltv_id: [program_dicts] }

    def resolve_epg_path(self, provider) -> str:
        """Resolves provider.epg into a local file path."""
        epg_src = provider.epg.strip() if provider.epg else ""
        if not epg_src:
            return None

        # Local file URI or raw filesystem path
        if epg_src.startswith("file://") or epg_src.startswith("/"):
            path = epg_src[7:] if epg_src.startswith("file://") else epg_src
            return os.path.expanduser(path)

        # Remote URL: cache locally in ~/.cache/hypnotix/epg/
        ext = ".xml.gz" if epg_src.endswith(".gz") else ".xml"
        return os.path.join(EPG_PATH, f"{slugify(provider.name)}{ext}")

    def is_cache_valid(self, local_path: str, ttl_hours: int = 24) -> bool:
        """Checks if the local cache file exists and is newer than TTL."""
        if not local_path or not os.path.exists(local_path):
            return False
        mtime = os.path.getmtime(local_path)
        return (time.time() - mtime) < (ttl_hours * 3600)

    def download_epg(self, epg_url: str, local_path: str) -> bool:
        """Downloads a remote EPG XML file to the local cache path."""
        try:
            req = urllib.request.Request(epg_url, headers={"User-Agent": self.user_agent})
            with urllib.request.urlopen(req, timeout=30) as resp, open(local_path, "wb") as out:
                out.write(resp.read())
            return True
        except Exception as e:
            print(f"[EPG] Download failed for {epg_url}: {e}")
            return False

    def load_epg_for_provider(self, provider, refresh: bool = False):
        """Fetches, loads, and matches EPG channels to provider.channels."""
        if not provider.epg:
            return

        local_path = self.resolve_epg_path(provider)
        if not local_path:
            return

        # Download if URL and cache is missing/expired
        if provider.epg.startswith("http://") or provider.epg.startswith("https://"):
            if refresh or not self.is_cache_valid(local_path):
                print(f"[EPG] Downloading EPG for provider '{provider.name}'...")
                self.download_epg(provider.epg, local_path)

        if not os.path.exists(local_path):
            print(f"[EPG] File not found: {local_path}")
            return

        print(f"[EPG] Parsing EPG file: {local_path}")
        xml_channels, self.programmes = XMLTVParser.parse_guide(local_path)

        # Match M3U channels with XMLTV channels
        matcher = IPTVSimpleMatcher(xml_channels)
        matched_count = 0
        for channel in provider.channels:
            channel.xmltv_id = matcher.match_channel(channel.tvg_id, channel.tvg_name, channel.title)
            if channel.xmltv_id:
                matched_count += 1

        print(f"[EPG] Matched {matched_count}/{len(provider.channels)} channels for provider '{provider.name}'")

    def get_current_and_next(self, xmltv_id: str):
        """Returns (now_playing, next_up) dicts for a channel at the current time."""
        if not xmltv_id or xmltv_id not in self.programmes:
            return None, None

        now = int(time.time())
        now_playing = None
        next_up = None

        for prog in self.programmes[xmltv_id]:
            if prog["start"] <= now < prog["stop"]:
                now_playing = prog
            elif prog["start"] >= now:
                if next_up is None or prog["start"] < next_up["start"]:
                    next_up = prog

        return now_playing, next_up
