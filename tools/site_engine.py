"""Markdown rendering and WordPress taxonomy helpers for publication snapshots."""
import json
import markdown
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor
from urllib.parse import urlsplit


class VideoMedia(Treeprocessor):
    def run(self,root):
        for node in root.iter('img'):
            if urlsplit(node.get('src','')).path.lower().endswith('.mp4'):
                node.tag='video';node.set('controls','');node.set('preload','metadata')
                node.set('playsinline','')
                # Publication snapshots must fit themes that only constrain images.
                # Keep the source aspect ratio for both landscape and portrait media.
                node.set('style','display:block;width:100%;max-width:100%;height:auto;')
                node.set('aria-label',node.attrib.pop('alt','Видео'))


class VideoExtension(Extension):
    def extendMarkdown(self,md):md.treeprocessors.register(VideoMedia(md),'granka-video',5)


def run_wp(args,site_dir,capture=True):
    raise RuntimeError('WordPress transport has not been configured')


def render_markdown(body):
    return markdown.markdown(body,extensions=['extra','codehilite','sane_lists','smarty',VideoExtension()],
        extension_configs={'codehilite':{'css_class':'highlight','guess_lang':False,'linenums':False}},output_format='html5')


def ensure_term(taxonomy,name,site_dir):
    existing=run_wp(['term','list',taxonomy,'--name='+name,'--field=term_id','--format=ids'],site_dir)
    if existing.strip():return int(existing.split()[0])
    return int(run_wp(['term','create',taxonomy,name,'--porcelain'],site_dir).strip())


def find_attachment_by_filename(filename,site_dir):
    literal=json.dumps('%/'+filename)
    php=('global $wpdb;$r=$wpdb->get_row($wpdb->prepare("SELECT ID,guid FROM $wpdb->posts '
         'WHERE post_type=\'attachment\' AND guid LIKE %s ORDER BY ID DESC LIMIT 1",'+literal+'));'
         'if($r) echo $r->ID."|".$r->guid;')
    value=run_wp(['eval',php],site_dir).strip()
    if '|' not in value:return None
    identifier,url=value.split('|',1)
    return int(identifier),url
