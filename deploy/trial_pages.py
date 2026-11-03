"""Shared, self-contained trial entry pages for the router and both portals."""
from __future__ import annotations

import re
from html import escape

STYLE = """:root {
  --bg: 0 0 0;
  --raised: 16 16 16;
  --field: 22 22 22;
  --line: 48 48 48;
  --ink: 255 255 255;
  --ink-dim: 181 181 181;
  --ink-faint: 146 146 146;
  --ink-mute: 104 104 104;
  --err: 255 128 128;
  --on-ink: 0 0 0;
  --brand-body: 255 255 255;
  --brand-eye: 0 0 0;
  --brand-pupil: 255 255 255;
  --sans: "SF Pro Display", "SF Pro Text", -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
  --mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: 255 255 255; --raised: 246 246 246; --field: 242 242 242; --line: 226 226 226;
    --ink: 0 0 0; --ink-dim: 82 82 82; --ink-faint: 110 110 110; --ink-mute: 150 150 150;
    --err: 190 40 40; --on-ink: 255 255 255;
    --brand-body: 0 0 0; --brand-eye: 255 255 255; --brand-pupil: 0 0 0;
  }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;background:rgb(var(--bg))}
body{margin:0;color:rgb(var(--ink));font:16px/1.6 var(--sans);-webkit-font-smoothing:antialiased;text-wrap:pretty}
a{color:inherit}
.ph{display:inline-block}
.page{max-width:1140px;margin:0 auto;padding:0 32px}
.head{display:flex;align-items:center;justify-content:space-between;height:68px;font-size:13px;color:rgb(var(--ink-faint))}
.head svg{display:block;height:20px;width:auto}
.b{fill:rgb(var(--brand-body))}.e{fill:rgb(var(--brand-eye))}.p{fill:rgb(var(--brand-pupil))}
.head-brand{display:flex;align-items:center;gap:14px;color:rgb(var(--ink))}
.head-brand span{color:rgb(var(--ink-faint))}
.head a{white-space:nowrap;text-decoration:none}
.head a:hover{color:rgb(var(--ink))}

/* 首屏 */
.hero{text-align:center;padding:96px 0 120px}
.kicker{margin:0 0 22px;font-weight:600;font-size:17px;letter-spacing:.01em}
h1{margin:0;font:700 clamp(44px,7.4vw,96px)/1.08 var(--sans);letter-spacing:-.03em}
h1 .dim{display:block;color:rgb(var(--ink-mute))}
.lede{max-width:30em;margin:34px auto 0;font-size:clamp(17px,1.6vw,20px);line-height:1.65;color:rgb(var(--ink-faint))}
.invite{margin:44px auto 0;max-width:520px}
.invite label{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.field{display:flex;gap:10px}
.field input{flex:1;min-width:0;height:52px;padding:0 22px;border:1px solid rgb(var(--line));border-radius:999px;background:rgb(var(--field));color:rgb(var(--ink));font:16px var(--sans)}
.field input::placeholder{color:rgb(var(--ink-mute))}
.field input:focus{outline:none;border-color:rgb(var(--ink-mute));background:rgb(var(--raised))}
.field input[aria-invalid=true]{border-color:rgb(var(--err))}
.field button{height:52px;padding:0 26px;border:0;border-radius:999px;background:rgb(var(--ink));color:rgb(var(--on-ink));font:500 16px var(--sans);white-space:nowrap;cursor:pointer}
.field button:hover{opacity:.88}
.field button:focus-visible{outline:2px solid rgb(var(--ink));outline-offset:3px}
.error{min-height:22px;margin:12px 0 0;color:rgb(var(--err));font-size:14px;line-height:22px}
.allowance{margin:6px 0 0;font-size:13px;color:rgb(var(--ink-mute))}
.other,.help{margin:8px 0 0;font-size:13px;color:rgb(var(--ink-mute))}
.other a{color:rgb(var(--ink-faint));text-underline-offset:3px}

/* 一次任务 */
.story{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:64px;padding:72px 0 120px;border-top:1px solid rgb(var(--line))}
.story h2{margin:0;font:700 clamp(36px,4.6vw,60px)/1.12 var(--sans);letter-spacing:-.025em}
.story h2 .dim{display:block;color:rgb(var(--ink-mute))}
.ask{margin:28px 0 0;font-size:19px;line-height:1.7;color:rgb(var(--ink-dim))}
.files{margin:10px 0 0;font:13px/1.6 var(--sans);color:rgb(var(--ink-mute))}
.result{display:flex;align-items:flex-end;gap:28px;margin:48px 0 0}
.result figure{margin:0}
.result strong{display:block;font:700 clamp(64px,8vw,104px)/1 var(--sans);letter-spacing:-.04em}
.result .was{color:rgb(var(--ink-mute))}
.result figcaption{margin-top:10px;font-size:13px;color:rgb(var(--ink-faint))}
.result i{font-style:normal;font-size:28px;color:rgb(var(--ink-mute));padding-bottom:46px}
.verdict{margin:22px 0 0;font-size:17px}
.log{list-style:none;margin:0;padding:0;align-self:start;border-top:1px solid rgb(var(--line))}
.log li{display:grid;grid-template-columns:3.4em 4.6em 1fr;gap:0 8px;padding:15px 0;border-bottom:1px solid rgb(var(--line));font-size:15px;color:rgb(var(--ink-dim))}
.log time{font:13px/1.9 var(--mono);color:rgb(var(--ink-mute))}
.log b{font-weight:600;color:rgb(var(--ink))}
.meta{margin:18px 0 0;font-size:13px;color:rgb(var(--ink-mute))}

/* 只在根地址出现：选版本 */
.versions{margin:44px auto 0;max-width:520px;text-align:left}
.label{margin:0 0 10px;font-size:13px;color:rgb(var(--ink-faint))}
.version{display:grid;grid-template-columns:7em 1fr auto;align-items:baseline;gap:16px;padding:18px 0;border-top:1px solid rgb(var(--line));text-decoration:none}
.version:last-of-type{border-bottom:1px solid rgb(var(--line))}
.version b{font:600 20px/1.3 var(--sans)}
.version span{color:rgb(var(--ink-faint))}
.version em{font-style:normal;color:rgb(var(--ink-faint))}
.version:hover em{color:rgb(var(--ink))}

@media (max-width:860px){
  .page{padding:0 16px}
  .head{height:56px}
  .hero{padding:56px 0 72px}
  .kicker{font-size:15px;margin-bottom:16px}
  .lede{margin-top:22px}
  .invite{margin-top:32px}
  h1{font-size:38px}
  .field{flex-direction:column}
  .field input{flex:none}
  .field button{width:100%}
  .story{grid-template-columns:minmax(0,1fr);gap:40px;padding:48px 0 72px}
  .log li{grid-template-columns:3.2em 4.4em 1fr;font-size:14px}
  .result{gap:18px}
  .result i{padding-bottom:36px}
}
"""

BRAND = """<svg viewBox="150 40 1160 390" role="img" aria-label="Argus"><g transform="translate(180 92) scale(.54)"><path class="b" fill-rule="evenodd" d="M352 112q0-30 30-30h28q30 0 30 30v320h-88v-52q-46 62-129 62Q66 442 66 266T228 88q80 0 124 56v-32ZM140 266q46-80 102-80t110 80q-54 80-110 80t-102-80Z"/><path class="e" d="M140 266q46-80 102-80t110 80q-54 80-110 80t-102-80Z"/><circle class="p" cx="244" cy="266" r="42"/><circle class="e" cx="262" cy="248" r="12"/></g><g class="b"><path d="M383 556Q394 556 409 555Q424 554 433 552L422 412Q415 414 401.5 415.5Q388 417 378 417Q340 417 305 403.5Q270 390 248.5 360Q227 330 227 278V0H78V546H191L213 454H220Q244 496 286 526Q328 556 383 556Z" transform="translate(444 334) scale(.36 -.36)"/><path d="M255 556Q356 556 413 476H417L429 546H555V-1Q555-118 486-179Q417-240 282-240Q224-240 174.5-233Q125-226 78-208V-89Q179-131 291-131Q406-131 406-7V4Q406 21 407.5 39Q409 57 410 71H406Q378 28 339 9Q300-10 251-10Q154-10 99.5 64.5Q45 139 45 272Q45 406 101 481Q157 556 255 556ZM302 435Q197 435 197 270Q197 107 304 107Q361 107 388.5 139.5Q416 172 416 253V271Q416 359 389 397Q362 435 302 435Z" transform="translate(617.52 334) scale(.36 -.36)"/><path d="M579 546V0H465L445 70H437Q411 28 365.5 9Q320-10 269-10Q181-10 128 37.5Q75 85 75 190V546H224V227Q224 169 245 139Q266 109 312 109Q380 109 405 155.5Q430 202 430 289V546Z" transform="translate(855.48 334) scale(.36 -.36)"/><path d="M459 162Q459 79 400.5 34.5Q342-10 226-10Q169-10 128-2.5Q87 5 46 22V145Q90 125 141 112Q192 99 231 99Q275 99 293.5 112Q312 125 312 146Q312 160 304.5 171Q297 182 272 196Q247 210 194 232Q143 254 110 275.5Q77 297 61 327.5Q45 358 45 404Q45 480 104 518Q163 556 261 556Q312 556 358 546Q404 536 453 513L408 406Q368 423 332 434.5Q296 446 259 446Q193 446 193 410Q193 397 201.5 386.5Q210 376 234.5 364Q259 352 307 332Q354 313 388 292.5Q422 272 440.5 241.5Q459 211 459 162Z" transform="translate(1102.08 334) scale(.36 -.36)"/></g></svg>"""

INTRO = """<p class="kicker">四个角色，一个团队</p>
<h1>一句话交代问题，<span class="dim">拿回能核对的结果。</span></h1>
<p class="lede"><span class="ph">查资料、写代码、跑实验、互相检查。</span><span class="ph">交给你的有结论，也有代码和运行记录。</span></p>"""

EXAMPLE = """<section class="story" aria-labelledby="story">
  <div>
    <h2 id="story">一次任务，<span class="dim">42 分钟交付。</span></h2>
    <p class="ask"><span class="ph">今年满意度比去年低了 6 分，</span><span class="ph">是真变差了，</span><span class="ph">还是来答题的人变了？</span></p>
    <p class="files">附件　问卷_2023.xlsx　问卷_2024.xlsx</p>
    <div class="result">
      <figure><strong class="was">6.1</strong><figcaption>原始差距</figcaption></figure>
      <i aria-hidden="true">→</i>
      <figure><strong>1.8</strong><figcaption>按构成加权后</figcaption></figure>
    </div>
    <p class="verdict">主要是新用户变多了；扣除后只低 1.8 分。</p>
  </div>
  <div>
    <ol class="log">
      <li><time>14:02</time><b>经理</b><span>定下交付：一页结论加可复跑的代码。</span></li>
      <li><time>14:05</time><b>规划者</b><span>先比两年答题人构成，再加权重算。</span></li>
      <li><time>14:09</time><b>工程师</b><span>清洗两份表，按年龄、地区加权。</span></li>
      <li><time>14:31</time><b>审阅者</b><span>查出 37 份重复提交，退回重算。</span></li>
      <li><time>14:44</time><b>工程师</b><span>重算完毕，写好结论和两张图。</span></li>
    </ol>
    <p class="meta">结论一页 · 图两张 · 代码与运行记录 · 花费约 1.9 美元</p>
  </div>
</section>"""

LANDING_CONTENT = """<nav class="versions" aria-labelledby="pick">
<p class="label" id="pick">选择版本</p>
<a class="version" href="/py/"><b>Python 版</b><span>现行版本，功能最全。</span><em>进入 →</em></a>
<a class="version" href="/js/"><b>JS 版</b><span>新的 TypeScript 核心，更轻、更快。</span><em>进入 →</em></a>
</nav><p class="allowance" id="invite-note"><span class="ph">每个邀请码有自己的工作区</span><span class="ph">和 {user} 美元额度，</span><span class="ph">用完即止，</span><span class="ph">不会重置。</span></p><p class="help">邀请码只在发给你的那个版本有效。</p>"""

LOGIN_CONTENT = """<form class="invite" method="post" action="/login">
<label for="invite">邀请码</label>
<div class="field"><input id="invite" name="invite" required autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="粘贴邀请码" aria-describedby="invite-error invite-note"{invalid} value="{invite}"><button>进入 →</button></div>
<p class="error" id="invite-error" role="alert">{error}</p>
<p class="allowance" id="invite-note"><span class="ph">每个邀请码有自己的工作区和 {user} 美元额度，</span><span class="ph">用完即止。</span></p><p class="other"><span class="ph">拿到的是{other}版的码？</span><a class="ph" href="/{other_path}/">去{other}版</a></p>
<p class="help">还没有邀请码？找给你链接的人要一个。</p>
</form>"""

LOGIN_SCRIPT = """<script>(()=>{
const form=document.querySelector('.invite'),input=document.getElementById('invite'),error=document.getElementById('invite-error');
input.addEventListener('input',()=>{input.removeAttribute('aria-invalid');error.textContent=''});
form.addEventListener('submit',event=>{input.value=input.value.trim();if(!input.value){event.preventDefault();input.focus()}});
if(matchMedia('(pointer:fine)').matches)input.focus({preventScroll:true});
})();</script>"""

def entry_page(user_usd: float, *, runtime: str | None = None, title: str | None = None,
               invite: str = "", error: bool = False, other: bool = True) -> str:
    version = "JS 版" if runtime == "js" else "Python 版"
    content = LANDING_CONTENT if runtime is None else LOGIN_CONTENT
    if not other:  # the site serves this version only: no link to one that is gone
        content = re.sub(r'<p class="other">.*?</p>', "", content)
    # Replace user-provided values last so their contents remain literal text.
    content = content.replace("{user}", f"{user_usd:g}")
    content = content.replace("{other_path}", "py" if runtime == "js" else "js")
    content = content.replace("{other}", " Python " if runtime == "js" else " JS ")
    content = content.replace("{error}", "这个邀请码不对，请核对后再贴一次。" if error else "")
    content = content.replace("{invalid}", ' aria-invalid="true"' if error else "")
    content = content.replace("{invite}", escape(invite, quote=True))
    heading = "Argus 试用" if runtime is None else f"邀请码 · Argus 试用（{version}）"
    if title:
        heading = title
    where = "" if runtime is None else f" / {version}"
    back = '<a href="/" rel="noreferrer">← 换版本</a>' if runtime is not None and other else '<span>邀请制试用</span>'
    script = LOGIN_SCRIPT if runtime is not None else ""
    return (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="color-scheme" content="dark light"><title>{escape(heading)}</title>'
            f'<style>{STYLE}</style></head><body><div class="page">'
            f'<header class="head"><div class="head-brand">{BRAND}<span>试用{where}</span></div>{back}</header>'
            f'<main><section class="hero">{INTRO}{content}</section>{EXAMPLE}</main></div>{script}</body></html>')
