"""Shared, self-contained trial entry pages for the router and both portals."""
from __future__ import annotations

from html import escape

STYLE = """:root {
  --bg: 245 245 244;
  --field: 255 255 255;
  --line: 220 222 225;
  --field-line: 167 172 179;
  --ink: 14 14 16;
  --ink-dim: 69 72 77;
  --ink-faint: 102 106 113;
  --accent: 176 141 60;
  --accent-ink: 133 102 31;
  --accent-hover: 111 84 25;
  --err: 163 58 58;
  --on-ink: 255 255 255;
  --brand-body: 0 0 0;
  --brand-eye: 255 255 255;
  --brand-pupil: 0 0 0;
  --sans: 'Geist Variable', 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', ui-sans-serif, system-ui, sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: 12 12 13; --field: 21 21 23; --line: 45 46 50; --field-line: 92 95 101;
    --ink: 242 242 240; --ink-dim: 186 189 195; --ink-faint: 142 146 152;
    --accent: 212 184 102; --accent-ink: 212 184 102; --accent-hover: 226 202 128;
    --err: 226 122 122; --on-ink: 12 12 13; --brand-body: 215 217 220; --brand-pupil: 32 35 38;
  }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;background:rgb(var(--bg))}
body{margin:0;color:rgb(var(--ink));font:16px/1.75 var(--sans);-webkit-font-smoothing:antialiased;text-wrap:pretty}
a{color:inherit}
.nw{white-space:nowrap}
.page{max-width:1280px;margin:0 auto;padding:0 56px}
.head{display:flex;align-items:center;justify-content:space-between;height:72px;border-bottom:1px solid rgb(var(--line));font-size:13px;color:rgb(var(--ink-faint));letter-spacing:.06em}
.head svg{display:block;height:22px;width:auto}
.b{fill:rgb(var(--brand-body))}.e{fill:rgb(var(--brand-eye))}.p{fill:rgb(var(--brand-pupil))}
main{display:grid;grid-template-columns:minmax(0,1fr) 420px;gap:0 64px;padding:72px 0}
h1{font:600 clamp(34px,3.8vw,54px)/1.2 var(--sans);letter-spacing:-.005em;margin:0}
.authors{font:14px/1.6 var(--sans);color:rgb(var(--ink-dim));letter-spacing:.06em;margin:22px 0 0}
.abstract{max-width:34em;color:rgb(var(--ink-dim));font-size:16.5px;margin:22px 0 0}
.label{font-size:12.5px;letter-spacing:.14em;color:rgb(var(--accent-ink));margin:0 0 10px}
.versions{margin-top:48px}
.version{display:grid;grid-template-columns:7.5em 1fr auto;align-items:baseline;gap:16px;padding:18px 0;border-top:1px solid rgb(var(--line));text-decoration:none;transition:background .15s}
.version:last-child{border-bottom:1px solid rgb(var(--line))}
.version b{font:600 22px/1.3 var(--sans)}
.version span{color:rgb(var(--ink-dim))}
.version em{font-style:normal;font-size:15px;color:rgb(var(--accent-ink));white-space:nowrap;transition:transform .15s}
.version:hover b,.version:focus-visible b{color:rgb(var(--accent-ink))}
.version:focus-visible{outline:2px solid rgb(var(--accent));outline-offset:4px}
.version:hover em,.version:focus-visible em{transform:translateX(3px)}
.note{position:relative;margin:36px 0 0;padding-top:14px;max-width:36em;font-size:13.5px;line-height:1.7;color:rgb(var(--ink-faint))}
.note::before{content:"";position:absolute;top:0;left:0;width:56px;border-top:1px solid rgb(var(--ink-faint))}

/* 示例：一页笔记的旁注栏 */
aside{border-left:1px solid rgb(var(--line));padding-left:44px;font-size:14.5px}
aside h2{font:600 19px/1.4 var(--sans);margin:0 0 26px}
aside h2 small{display:block;font:12.5px/1.6 var(--sans);letter-spacing:.14em;color:rgb(var(--accent-ink));margin-bottom:4px}
aside h3{display:flex;align-items:center;gap:12px;font-size:12.5px;font-weight:400;letter-spacing:.14em;color:rgb(var(--ink-faint));margin:28px 0 10px}
aside h3::after{content:"";flex:1;border-top:1px solid rgb(var(--line))}
blockquote{margin:0;padding-left:14px;border-left:2px solid rgb(var(--accent));font:19px/1.65 var(--sans)}
.files{margin:8px 0 0 16px;font:12.5px/1.6 var(--mono);color:rgb(var(--ink-faint))}
.log{list-style:none;margin:0;padding:0}
.log li{display:grid;grid-template-columns:3.6em 4.3em 1fr;padding:5px 0;color:rgb(var(--ink-dim))}
.log time{font:12.5px/2 var(--mono);color:rgb(var(--ink-faint))}
.log b{font-weight:600;color:rgb(var(--ink))}
.result{display:flex;align-items:flex-end;gap:18px;margin:4px 0 10px}
.result figure{margin:0}
.result strong{display:block;font:600 40px/1.1 var(--sans)}
.result .was{color:rgb(var(--ink-faint))}
.result figcaption{font-size:12.5px;color:rgb(var(--ink-faint))}
.result i{font-style:normal;font-size:22px;color:rgb(var(--accent));padding-bottom:22px}
aside p{margin:0}
.meta{margin-top:18px;font:12.5px/1.6 var(--mono);color:rgb(var(--ink-faint))}

@media (max-width:960px){
  .page{padding:0 24px}
  .head{height:56px}
  main{grid-template-columns:minmax(0,1fr);padding:44px 0 56px}
  .version{grid-template-columns:1fr auto;gap:2px 12px;padding:16px 0}
  .version span{grid-row:2;font-size:14.5px}
  .version em{grid-row:1/3;grid-column:2;align-self:center}
  .log li{grid-template-columns:3.3em 4.1em 1fr;font-size:14px}
  aside{border-left:0;padding:36px 0 0;margin-top:48px;border-top:1px solid rgb(var(--ink))}
}

section,aside{min-width:0}
.head-brand{display:flex;align-items:center;gap:14px;color:rgb(var(--ink));letter-spacing:0}
.head-brand span{color:rgb(var(--ink-faint))}
.head a{white-space:nowrap}
.invite{margin-top:32px;max-width:520px}
.invite h2{margin:0 0 16px;font-size:24px;line-height:1.3}
.invite label{display:block;margin-bottom:8px;color:rgb(var(--ink-dim));font-size:14px}
.field{display:flex;gap:8px}
.field input{flex:1;min-width:0;height:46px;padding:0 12px;border:1px solid rgb(var(--field-line));border-radius:6px;background:rgb(var(--field));color:rgb(var(--ink));font:16px var(--sans)}
.field input::placeholder{color:rgb(var(--ink-faint))}
.field input:focus{outline:none;border-color:rgb(var(--accent));box-shadow:0 0 0 3px rgb(var(--accent) / .2)}
.field input[aria-invalid=true]{border-color:rgb(var(--err))}
.field input[aria-invalid=true]:focus{box-shadow:0 0 0 3px rgb(var(--err) / .15)}
.field button{height:46px;padding:0 22px;border:0;border-radius:6px;background:rgb(var(--ink));color:rgb(var(--on-ink));font:500 15px var(--sans);white-space:nowrap;cursor:pointer}
.field button:hover{opacity:.85}
.field button:focus-visible{outline:2px solid rgb(var(--accent));outline-offset:3px}
.error{min-height:24px;margin:8px 0 0;color:rgb(var(--err));font-size:14px;line-height:24px}
.allowance{font-size:13.5px;color:rgb(var(--ink-faint));margin:18px 0 0;line-height:1.9}
.ph{display:inline-block}
.other{margin:16px 0 0;font-size:14px;color:rgb(var(--ink-dim))}
.other a{color:rgb(var(--accent-ink));text-underline-offset:3px}
.help{margin:12px 0 0;font-size:13px;color:rgb(var(--ink-faint))}
.files,.meta{font-family:var(--sans)}
.log time{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.mobile-more{display:none}
@media(max-width:960px){
  main{padding-top:36px}
  h1{font-size:32px;line-height:1.3;letter-spacing:-.025em}
  .abstract{font-size:15px;margin-top:18px}
  .authors{font-size:12px;margin-top:16px;letter-spacing:.03em}
  .versions{margin-top:32px}
  .invite{margin-top:28px}
  aside{margin-top:36px;padding-top:28px}
  .log li:nth-child(n+4){display:none}
  .mobile-more{display:block;margin-top:10px;color:rgb(var(--ink-faint));font-size:12.5px}
  .field button{padding:0 16px}
}
"""

BRAND = """<svg viewBox="150 40 1160 390" role="img" aria-label="Argus"><g transform="translate(180 92) scale(.54)"><path class="b" fill-rule="evenodd" d="M352 112q0-30 30-30h28q30 0 30 30v320h-88v-52q-46 62-129 62Q66 442 66 266T228 88q80 0 124 56v-32ZM140 266q46-80 102-80t110 80q-54 80-110 80t-102-80Z"/><path class="e" d="M140 266q46-80 102-80t110 80q-54 80-110 80t-102-80Z"/><circle class="p" cx="244" cy="266" r="42"/><circle class="e" cx="262" cy="248" r="12"/></g><g class="b"><path d="M383 556Q394 556 409 555Q424 554 433 552L422 412Q415 414 401.5 415.5Q388 417 378 417Q340 417 305 403.5Q270 390 248.5 360Q227 330 227 278V0H78V546H191L213 454H220Q244 496 286 526Q328 556 383 556Z" transform="translate(444 334) scale(.36 -.36)"/><path d="M255 556Q356 556 413 476H417L429 546H555V-1Q555-118 486-179Q417-240 282-240Q224-240 174.5-233Q125-226 78-208V-89Q179-131 291-131Q406-131 406-7V4Q406 21 407.5 39Q409 57 410 71H406Q378 28 339 9Q300-10 251-10Q154-10 99.5 64.5Q45 139 45 272Q45 406 101 481Q157 556 255 556ZM302 435Q197 435 197 270Q197 107 304 107Q361 107 388.5 139.5Q416 172 416 253V271Q416 359 389 397Q362 435 302 435Z" transform="translate(617.52 334) scale(.36 -.36)"/><path d="M579 546V0H465L445 70H437Q411 28 365.5 9Q320-10 269-10Q181-10 128 37.5Q75 85 75 190V546H224V227Q224 169 245 139Q266 109 312 109Q380 109 405 155.5Q430 202 430 289V546Z" transform="translate(855.48 334) scale(.36 -.36)"/><path d="M459 162Q459 79 400.5 34.5Q342-10 226-10Q169-10 128-2.5Q87 5 46 22V145Q90 125 141 112Q192 99 231 99Q275 99 293.5 112Q312 125 312 146Q312 160 304.5 171Q297 182 272 196Q247 210 194 232Q143 254 110 275.5Q77 297 61 327.5Q45 358 45 404Q45 480 104 518Q163 556 261 556Q312 556 358 546Q404 536 453 513L408 406Q368 423 332 434.5Q296 446 259 446Q193 446 193 410Q193 397 201.5 386.5Q210 376 234.5 364Q259 352 307 332Q354 313 388 292.5Q422 272 440.5 241.5Q459 211 459 162Z" transform="translate(1102.08 334) scale(.36 -.36)"/></g></svg>"""

INTRO = """<h1>一句话交代问题，<br>拿回能核对的结果。</h1>
<p class="authors">经理 · 规划者 · 工程师 · 审阅者</p>
<p class="abstract">Argus 是一个由 AI 组成的研究小组，<span class="ph">四位分工合作：</span><span class="ph">查资料、</span><span class="ph">写代码、</span><span class="ph">跑实验、</span><span class="ph">互相检查。</span>交给你的不只有结论，<span class="ph">还有代码和运行记录。</span></p>"""

EXAMPLE = """<aside aria-labelledby="example">
  <h2 id="example"><small>示例</small>一次任务的记录</h2>

  <h3>任务</h3>
  <blockquote>今年满意度比去年低了 6 分，<span class="ph">是真变差了，</span><span class="ph">还是来答题的人变了？</span></blockquote>
  <p class="files">附件　问卷_2023.xlsx　问卷_2024.xlsx</p>

  <h3>经过</h3>
  <ol class="log">
    <li><time>14:02</time><b>经理</b><span>定下交付：一页结论加可复跑的代码。</span></li>
    <li><time>14:05</time><b>规划者</b><span>先比两年答题人构成，再加权重算。</span></li>
    <li><time>14:09</time><b>工程师</b><span>清洗两份表，按年龄、地区加权。</span></li>
    <li><time>14:31</time><b>审阅者</b><span>查出 37 份重复提交，退回重算。</span></li>
    <li><time>14:44</time><b>工程师</b><span>重算完毕，写好结论和两张图。</span></li>
  </ol>
  <p class="mobile-more">完整记录见进入后的工作台。</p>

  <h3>交付</h3>
  <div class="result">
    <figure><strong class="was">6.1</strong><figcaption>原始差距</figcaption></figure>
    <i aria-hidden="true">→</i>
    <figure><strong>1.8</strong><figcaption>按构成加权后</figcaption></figure>
  </div>
  <p>主要是新用户变多了；扣除后只低 1.8 分。</p>
  <p class="meta">结论一页 · 图两张 · 代码与运行记录<br>用时 42 分钟 · 花费约 1.9 美元</p>
</aside>"""

LANDING_CONTENT = """<nav class="versions" aria-labelledby="pick">
<p class="label" id="pick">选择版本</p>
<a class="version" href="/py/"><b>Python 版</b><span>现行版本，功能最全。</span><em>进入 →</em></a>
<a class="version" href="/js/"><b>JS 版</b><span>新的 TypeScript 核心，更轻、更快。</span><em>进入 →</em></a>
</nav><p class="allowance" id="invite-note"><span class="ph">每个邀请码有自己的工作区</span><span class="ph">和 {user} 美元额度，</span><span class="ph">用完即止，</span><span class="ph">不会重置。</span></p><p class="help">邀请码只在发给你的那个版本有效。</p>"""

LOGIN_CONTENT = """<form class="invite" method="post" action="/login">
<h2>输入邀请码</h2><label for="invite">邀请码</label>
<div class="field"><input id="invite" name="invite" required autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="粘贴发给你的邀请码" aria-describedby="invite-error invite-note"{invalid} value="{invite}"><button>进入 →</button></div>
<p class="error" id="invite-error" role="alert">{error}</p>
<p class="allowance" id="invite-note"><span class="ph">每个邀请码有自己的工作区</span><span class="ph">和 {user} 美元额度，</span><span class="ph">用完即止，</span><span class="ph">不会重置。</span></p><p class="other"><span class="ph">拿到的是{other}版的码？</span><a class="ph" href="/{other_path}/">去{other}版</a></p>
<p class="help">还没有邀请码？找给你链接的人要一个。</p>
</form>"""

LOGIN_SCRIPT = """<script>(()=>{
const form=document.querySelector('.invite'),input=document.getElementById('invite'),error=document.getElementById('invite-error');
input.addEventListener('input',()=>{input.removeAttribute('aria-invalid');error.textContent=''});
form.addEventListener('submit',event=>{input.value=input.value.trim();if(!input.value){event.preventDefault();input.focus()}});
if(matchMedia('(pointer:fine)').matches)input.focus({preventScroll:true});
})();</script>"""

def entry_page(user_usd: float, *, runtime: str | None = None, title: str | None = None,
               invite: str = "", error: bool = False) -> str:
    version = "JS 版" if runtime == "js" else "Python 版"
    content = LANDING_CONTENT if runtime is None else LOGIN_CONTENT
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
    back = '<a href="/" rel="noreferrer">← 换版本</a>' if runtime is not None else '<span>邀请制试用</span>'
    script = LOGIN_SCRIPT if runtime is not None else ""
    return (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="color-scheme" content="light dark"><title>{escape(heading)}</title>'
            f'<style>{STYLE}</style></head><body><div class="page">'
            f'<header class="head"><div class="head-brand">{BRAND}<span>试用{where}</span></div>{back}</header>'
            f'<main><section>{INTRO}{content}</section>{EXAMPLE}</main></div>{script}</body></html>')
