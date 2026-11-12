#!/usr/bin/env python3
"""Usage: render.py frames t1 t2 ...  |  render.py scene IDX START END OUT.mp4"""
import sys, re, asyncio, subprocess, os
from playwright.async_api import async_playwright

D = os.path.dirname(os.path.abspath(__file__))
pin = open('/tmp/gen/pin.svg').read()
pin = re.sub(r'^<svg[^>]*>', '', pin.strip()).replace('</svg>', '')
pin = pin.replace('<rect width="64" height="64" rx="14" fill="url(#bg)"/>', '')
html = open(os.path.join(D, os.environ.get('VF','video.html')), encoding='utf-8').read().replace('__PIN__', pin.replace('`', ''))
open(os.path.join(D, 'built.html'), 'w', encoding='utf-8').write(html)
FPS = 30
W, H = int(os.environ.get('W', 1080)), int(os.environ.get('H', 1920))
if H != 1920:
    top, bot = 80, 1610
    k = H / (bot - top)
    fit = f'''<style>html,body{{width:{W}px!important;height:{H}px!important}}
#stage{{position:absolute;left:50%;top:{60 - top * k + 0:.1f}px;width:1080px;height:1920px;transform-origin:50% 0;transform:translateX(-50%) scale({k:.4f})}}</style>
<script>addEventListener('DOMContentLoaded',()=>{{}});</script>'''
    html = html.replace('</head>', fit + '</head>')
    html = html.replace('<div id="bgl"></div><div id="parts"></div>', '<div id="bgl"></div><div id="parts"></div><div id="stage">', 1)
    html = html.replace('</div><div id="vign">', '</div></div><div id="vign">', 1)
    open(os.path.join(D, 'built.html'), 'w', encoding='utf-8').write(html)

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
        pg = await b.new_page(viewport={'width': W, 'height': H})
        await pg.goto('file://' + os.path.join(D, 'built.html'))
        await pg.evaluate('document.fonts.ready')
        await pg.wait_for_timeout(300)
        if sys.argv[1] == 'frames':
            for t in sys.argv[2:]:
                await pg.evaluate(f'render({t})')
                await pg.screenshot(path=f'/tmp/vid/f_{t}.jpg', type='jpeg', quality=85)
        else:
            _, _, idx, a, z, out = sys.argv
            a, z = float(a), float(z)
            n0, n1 = round(a * FPS), round(z * FPS)
            tmp = out.replace('.mp4', '.part.mp4')
            ff = subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'image2pipe', '-c:v', 'mjpeg', '-framerate', str(FPS), '-i', '-',
                                   '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '17', '-pix_fmt', 'yuv420p', '-r', str(FPS), tmp], stdin=subprocess.PIPE)
            for f in range(n0, n1):
                await pg.evaluate(f'render({f / FPS})')
                ff.stdin.write(await pg.screenshot(type='jpeg', quality=92))
            ff.stdin.close()
            if ff.wait() == 0: os.replace(tmp, out)
            print('scene', idx, n1 - n0, 'frames')
        await b.close()

asyncio.run(main())
