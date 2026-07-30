from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.beatport import search as search_beatport
from core.bandcamp import search as search_bandcamp

app = Flask(__name__)

# Repositorio no GitHub que roda o build automatico (.github/workflows/build-desktop.yml).
# Troque para "SEU_USUARIO/NOME_DO_REPO" assim que o repositorio existir no GitHub.
# Os links abaixo apontam pro release "latest", que o workflow sobrescreve a cada
# push na main - o arquivo baixado muda, a URL nunca muda.
GITHUB_REPO = "NiraslabDEV/Music-Agregador"
MAC_DOWNLOAD_URL = f"https://github.com/{GITHUB_REPO}/releases/latest/download/MusicAggregator-macOS.dmg"
WINDOWS_DOWNLOAD_URL = f"https://github.com/{GITHUB_REPO}/releases/latest/download/MusicAggregator-Windows.zip"


HTML_PAGE = """
<!doctype html>
<html lang=\"pt-BR\">
<head>
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>Music Aggregator — preço em cada loja + a melhor fonte grátis</title>
<meta name=\"description\" content=\"Busque uma faixa e compare preço no Beatport, no Bandcamp e a melhor fonte grátis no Soulseek — tudo num lugar só.\" />
<style>
  :root {
    color-scheme: light dark;
    --bg: #F3F2FB;
    --bg-2: #EAE7FA;
    --surface: #FFFFFF;
    --surface-2: #FAFAFF;
    --ink: #1E1B33;
    --muted: #6B6684;
    --border: rgba(30,27,51,0.09);
    --primary: #4F46E5;
    --primary-dark: #3730A3;
    --primary-soft: #EEF0FE;
    --violet: #7C3AED;
    --beatport: #E11D74;
    --beatport-soft: #FDEBF3;
    --bandcamp: #0F91A8;
    --bandcamp-soft: #E6F6F9;
    --free: #16A34A;
    --free-soft: #E9F9EF;
    --shadow: 0 20px 45px -20px rgba(46, 16, 101, 0.28);
    --radius-lg: 20px;
    --radius-md: 14px;
    --radius-sm: 10px;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #131024;
      --bg-2: #1A1530;
      --surface: #1D1832;
      --surface-2: #221C3B;
      --ink: #F3F1FC;
      --muted: #AAA3C9;
      --border: rgba(255,255,255,0.09);
      --primary: #8B85F0;
      --primary-dark: #6F66E0;
      --primary-soft: rgba(139,133,240,0.14);
      --violet: #A98BF5;
      --beatport: #F871B0;
      --beatport-soft: rgba(248,113,176,0.14);
      --bandcamp: #5FD6EA;
      --bandcamp-soft: rgba(95,214,234,0.14);
      --free: #4ADE80;
      --free-soft: rgba(74,222,128,0.14);
      --shadow: 0 20px 45px -18px rgba(0,0,0,0.55);
    }
  }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  @media (prefers-reduced-motion: reduce) {
    html { scroll-behavior: auto; }
    * { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
  }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, Helvetica, Arial, sans-serif;
    background:
      radial-gradient(1100px 480px at 12% -10%, var(--bg-2), transparent),
      radial-gradient(900px 420px at 100% 0%, var(--primary-soft), transparent),
      var(--bg);
    color: var(--ink);
    line-height: 1.55;
    -webkit-font-smoothing: antialiased;
  }
  a { color: inherit; }
  .wrap { max-width: 1080px; margin: 0 auto; padding: 0 20px; }
  img, svg { display: block; }
  button, a.btn { cursor: pointer; }
  :focus-visible { outline: 2px solid var(--primary); outline-offset: 3px; border-radius: 6px; }

  /* ---------- nav ---------- */
  .nav {
    position: sticky; top: 0; z-index: 50;
    backdrop-filter: blur(10px);
    background: color-mix(in srgb, var(--bg) 78%, transparent);
    border-bottom: 1px solid var(--border);
  }
  .nav-row { display: flex; align-items: center; gap: 12px; padding: 14px 20px; }
  .brand { display: flex; align-items: center; gap: 10px; font-weight: 700; letter-spacing: -0.01em; text-decoration: none; }
  .brand-badge {
    width: 32px; height: 32px; border-radius: 10px; flex: none;
    background: linear-gradient(135deg, var(--primary-dark), var(--primary) 55%, var(--violet));
    display: flex; align-items: center; justify-content: center; color: #fff;
  }
  .nav-spacer { flex: 1; }
  .nav-links { display: flex; align-items: center; gap: 22px; font-size: 14px; color: var(--muted); }
  .nav-links a { text-decoration: none; transition: color .15s ease; }
  .nav-links a:hover { color: var(--ink); }
  .nav-cta {
    display: inline-flex; align-items: center; gap: 8px;
    background: var(--ink); color: var(--bg); border: none;
    padding: 9px 16px; border-radius: 999px; font-size: 13.5px; font-weight: 600;
    text-decoration: none; transition: transform .15s ease, opacity .15s ease;
  }
  .nav-cta:hover { transform: translateY(-1px); opacity: .9; }
  @media (max-width: 720px) { .nav-links { display: none; } }

  /* ---------- hero ---------- */
  .hero { padding: 76px 0 56px; text-align: center; }
  .eyebrow {
    display: inline-flex; align-items: center; gap: 8px;
    background: var(--surface); border: 1px solid var(--border);
    padding: 6px 14px; border-radius: 999px; font-size: 13px; color: var(--muted);
    box-shadow: var(--shadow);
  }
  .eyebrow .dot { width: 7px; height: 7px; border-radius: 999px; background: var(--free); box-shadow: 0 0 0 3px var(--free-soft); }
  h1.hero-title {
    margin: 22px auto 0; max-width: 760px;
    font-size: clamp(32px, 5.4vw, 54px); line-height: 1.08; letter-spacing: -0.03em; font-weight: 800;
  }
  h1.hero-title .grad {
    background: linear-gradient(100deg, var(--primary-dark), var(--primary) 45%, var(--violet));
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
  .hero-sub { max-width: 600px; margin: 18px auto 0; color: var(--muted); font-size: 17.5px; }
  .hero-ctas { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; margin-top: 34px; }
  .btn {
    display: inline-flex; align-items: center; gap: 10px;
    padding: 14px 22px; border-radius: 14px; font-weight: 600; font-size: 15px;
    text-decoration: none; border: 1px solid transparent;
    transition: transform .15s ease, box-shadow .15s ease, opacity .15s ease;
  }
  .btn:hover { transform: translateY(-2px); }
  .btn:active { transform: translateY(0); }
  .btn-mac { background: #0B0B12; color: #fff; box-shadow: 0 14px 30px -12px rgba(0,0,0,0.5); }
  .btn-win { background: linear-gradient(135deg, var(--primary-dark), var(--primary)); color: #fff; box-shadow: 0 14px 30px -12px rgba(79,70,229,0.55); }
  .btn-ghost { background: var(--surface); color: var(--ink); border-color: var(--border); }
  .btn small { display: block; font-weight: 500; opacity: .72; font-size: 11.5px; margin-top: 1px; }
  .hero-note { margin-top: 16px; font-size: 13px; color: var(--muted); }
  .hero-note svg { display: inline; vertical-align: -3px; margin-right: 5px; }

  .hero-strip { display: flex; gap: 26px; justify-content: center; flex-wrap: wrap; margin-top: 46px; color: var(--muted); font-size: 13px; }
  .hero-strip span { display: inline-flex; align-items: center; gap: 7px; }
  .hero-strip svg { color: var(--free); }

  /* ---------- sections shared ---------- */
  section { padding: 64px 0; }
  .section-head { text-align: center; max-width: 620px; margin: 0 auto 44px; }
  .kicker { color: var(--primary); font-weight: 700; font-size: 12.5px; letter-spacing: .08em; text-transform: uppercase; }
  .section-title { font-size: clamp(24px, 3.4vw, 34px); letter-spacing: -0.02em; font-weight: 800; margin: 10px 0 0; }
  .section-sub { color: var(--muted); margin-top: 12px; font-size: 15.5px; }

  /* ---------- features ---------- */
  .features { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; }
  @media (max-width: 860px) { .features { grid-template-columns: 1fr; } }
  .feature {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg);
    padding: 26px; box-shadow: var(--shadow); transition: transform .18s ease, box-shadow .18s ease;
  }
  .feature:hover { transform: translateY(-4px); }
  .feature-icon { width: 44px; height: 44px; border-radius: 12px; display: flex; align-items: center; justify-content: center; margin-bottom: 16px; }
  .feature h3 { margin: 0 0 8px; font-size: 17.5px; letter-spacing: -0.01em; }
  .feature p { margin: 0; color: var(--muted); font-size: 14.5px; }
  .feature.beatport .feature-icon { background: var(--beatport-soft); color: var(--beatport); }
  .feature.bandcamp .feature-icon { background: var(--bandcamp-soft); color: var(--bandcamp); }
  .feature.free .feature-icon { background: var(--free-soft); color: var(--free); }

  /* ---------- how it works ---------- */
  .steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 28px; counter-reset: step; }
  @media (max-width: 860px) { .steps { grid-template-columns: 1fr; } }
  .step { position: relative; padding-left: 52px; }
  .step .num {
    position: absolute; left: 0; top: 0; width: 38px; height: 38px; border-radius: 11px;
    background: var(--primary-soft); color: var(--primary); font-weight: 800; font-size: 15px;
    display: flex; align-items: center; justify-content: center;
  }
  .step h3 { margin: 2px 0 6px; font-size: 16px; }
  .step p { margin: 0; color: var(--muted); font-size: 14.5px; }

  /* ---------- demo / search ---------- */
  .demo-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg);
    box-shadow: var(--shadow); padding: 28px; max-width: 760px; margin: 0 auto;
  }
  .demo-form { display: flex; gap: 10px; flex-wrap: wrap; }
  .demo-form input {
    flex: 1; min-width: 220px; padding: 14px 16px; border-radius: var(--radius-md);
    border: 1px solid var(--border); background: var(--surface-2); color: var(--ink); font-size: 15.5px;
  }
  .demo-form input:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-soft); }
  .demo-form button {
    display: inline-flex; align-items: center; gap: 8px;
    border: 0; background: linear-gradient(135deg, var(--primary-dark), var(--primary));
    color: #fff; padding: 14px 20px; border-radius: var(--radius-md); font-size: 15px; font-weight: 600;
    transition: opacity .15s ease;
  }
  .demo-form button:hover { opacity: .92; }
  .demo-form button:disabled { opacity: .6; cursor: default; }
  .demo-status { min-height: 20px; margin-top: 14px; font-size: 13.5px; color: var(--muted); }
  .demo-results { display: grid; gap: 12px; margin-top: 10px; }
  .result-row {
    display: flex; align-items: center; gap: 14px; padding: 14px; border-radius: var(--radius-md);
    border: 1px solid var(--border); background: var(--surface-2); text-decoration: none; color: inherit;
    transition: border-color .15s ease, transform .15s ease;
  }
  .result-row:hover { border-color: var(--primary); transform: translateX(2px); }
  .result-cover { width: 46px; height: 46px; border-radius: 9px; flex: none; object-fit: cover; background: var(--primary-soft); }
  .result-info { flex: 1; min-width: 0; }
  .result-title { font-weight: 600; font-size: 14.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .result-artist { color: var(--muted); font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .result-tag {
    flex: none; font-size: 11.5px; font-weight: 700; padding: 5px 10px; border-radius: 999px;
    text-transform: uppercase; letter-spacing: .03em;
  }
  .result-tag.beatport { background: var(--beatport-soft); color: var(--beatport); }
  .result-tag.bandcamp { background: var(--bandcamp-soft); color: var(--bandcamp); }
  .result-price { flex: none; font-weight: 700; font-size: 14px; min-width: 64px; text-align: right; }
  .empty-hint { text-align: center; color: var(--muted); font-size: 14px; padding: 18px 0; }

  /* ---------- download ---------- */
  .download-panel {
    background: linear-gradient(135deg, var(--primary-dark), var(--primary) 55%, var(--violet));
    border-radius: 26px; padding: 52px 32px; text-align: center; color: #fff;
    box-shadow: 0 30px 60px -25px rgba(79,70,229,0.55);
  }
  .download-panel h2 { margin: 0; font-size: clamp(24px, 3.6vw, 32px); letter-spacing: -0.02em; }
  .download-panel p { color: rgba(255,255,255,0.82); max-width: 480px; margin: 12px auto 0; font-size: 15px; }
  .download-ctas { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; margin-top: 30px; }
  .download-panel .btn-ghost { background: rgba(255,255,255,0.12); color: #fff; border-color: rgba(255,255,255,0.28); }
  .download-panel .btn-mac { background: #0B0B12; }
  .download-panel .btn-win { background: rgba(255,255,255,0.96); color: var(--primary-dark); }
  .download-foot { margin-top: 22px; font-size: 12.5px; color: rgba(255,255,255,0.75); }

  /* ---------- faq ---------- */
  .faq-list { max-width: 720px; margin: 0 auto; display: grid; gap: 10px; }
  .faq-item { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-md); overflow: hidden; }
  .faq-item summary {
    list-style: none; cursor: pointer; padding: 18px 20px; font-weight: 600; font-size: 15px;
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
  }
  .faq-item summary::-webkit-details-marker { display: none; }
  .faq-item summary svg { flex: none; transition: transform .2s ease; color: var(--muted); }
  .faq-item[open] summary svg { transform: rotate(180deg); }
  .faq-item .faq-body { padding: 0 20px 18px; color: var(--muted); font-size: 14.5px; }

  /* ---------- footer ---------- */
  footer { border-top: 1px solid var(--border); padding: 34px 0; }
  .footer-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; color: var(--muted); font-size: 13.5px; }
  .footer-row a { text-decoration: none; }
  .footer-row a:hover { color: var(--ink); }
</style>
</head>
<body>

<nav class=\"nav\">
  <div class=\"wrap nav-row\">
    <a class=\"brand\" href=\"#top\">
      <span class=\"brand-badge\">
        <svg width=\"17\" height=\"17\" viewBox=\"0 0 24 24\" fill=\"none\"><path d=\"M9 18V5l12-2v13\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><circle cx=\"6\" cy=\"18\" r=\"3\" stroke=\"currentColor\" stroke-width=\"2\"/><circle cx=\"18\" cy=\"16\" r=\"3\" stroke=\"currentColor\" stroke-width=\"2\"/></svg>
      </span>
      Music Aggregator
    </a>
    <div class=\"nav-spacer\"></div>
    <div class=\"nav-links\">
      <a href=\"#features\">Fontes</a>
      <a href=\"#demo\">Testar agora</a>
      <a href=\"#faq\">Perguntas</a>
    </div>
    <a class=\"nav-cta\" href=\"#download\">
      <svg width=\"14\" height=\"14\" viewBox=\"0 0 24 24\" fill=\"none\"><path d=\"M12 3v12m0 0 4-4m-4 4-4-4M5 21h14\" stroke=\"currentColor\" stroke-width=\"2.2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/></svg>
      Baixar app
    </a>
  </div>
</nav>

<main id=\"top\">

  <section class=\"hero wrap\">
    <span class=\"eyebrow\"><span class=\"dot\"></span> Sempre a versão mais nova — sem reinstalar nada na mão</span>
    <h1 class=\"hero-title\">Preço em cada loja. <span class=\"grad\">E a melhor fonte grátis.</span></h1>
    <p class=\"hero-sub\">Digite uma faixa e veja, lado a lado, o preço no Beatport, no Bandcamp e — no app de mesa — a melhor opção pra baixar de graça no Soulseek.</p>
    <div class=\"hero-ctas\">
      <a class=\"btn btn-mac\" href=\"__MAC_DOWNLOAD_URL__\">
        <svg width=\"18\" height=\"18\" viewBox=\"0 0 24 24\" fill=\"currentColor\"><path d=\"M16.365 1.43c0 1.14-.47 2.11-1.19 2.86-.79.83-2.03 1.45-3.05 1.37-.13-1.09.43-2.24 1.15-2.96.78-.81 2.13-1.42 3.09-1.27ZM20.9 17.4c-.52 1.2-.76 1.74-1.42 2.8-.92 1.48-2.22 3.33-3.83 3.35-1.43.02-1.8-.93-3.74-.92-1.94.01-2.35.94-3.78.92-1.6-.02-2.83-1.68-3.75-3.16-2.57-4.1-2.84-8.9-1.25-11.46 1.13-1.82 2.9-2.89 4.57-2.89 1.7 0 2.77 1 4.18 1 1.36 0 2.2-1 4.18-1 1.49 0 3.07.81 4.19 2.21-3.69 2.02-3.09 7.28.65 9.15Z\"/></svg>
        Baixar para Mac
        <small>macOS 11 ou mais novo</small>
      </a>
      <a class=\"btn btn-win\" href=\"__WINDOWS_DOWNLOAD_URL__\">
        <svg width=\"17\" height=\"17\" viewBox=\"0 0 24 24\" fill=\"currentColor\"><path d=\"M3 5.5 10.4 4.5V11.4H3V5.5ZM11.3 4.35 21 3V11.3H11.3V4.35ZM3 12.4H10.4V19.4L3 18.4V12.4ZM11.3 12.4H21V20.9L11.3 19.6V12.4Z\"/></svg>
        Baixar para Windows
        <small>Windows 10/11</small>
      </a>
      <a class=\"btn btn-ghost\" href=\"#demo\">
        Testar no navegador
      </a>
    </div>
    <p class=\"hero-note\">
      <svg width=\"14\" height=\"14\" viewBox=\"0 0 24 24\" fill=\"none\"><circle cx=\"12\" cy=\"12\" r=\"9\" stroke=\"currentColor\" stroke-width=\"2\"/><path d=\"M12 8v4l3 2\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\"/></svg>
      No Mac, na primeira abertura: clique com o botão direito no app → \"Abrir\" (ele ainda não tem assinatura da Apple).
    </p>
    <div class=\"hero-strip\">
      <span><svg width=\"15\" height=\"15\" viewBox=\"0 0 24 24\" fill=\"none\"><path d=\"m5 13 4 4L19 7\" stroke=\"currentColor\" stroke-width=\"2.4\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/></svg> Gratuito</span>
      <span><svg width=\"15\" height=\"15\" viewBox=\"0 0 24 24\" fill=\"none\"><path d=\"m5 13 4 4L19 7\" stroke=\"currentColor\" stroke-width=\"2.4\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/></svg> Sem cadastro pra buscar</span>
      <span><svg width=\"15\" height=\"15\" viewBox=\"0 0 24 24\" fill=\"none\"><path d=\"m5 13 4 4L19 7\" stroke=\"currentColor\" stroke-width=\"2.4\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/></svg> Build atualiza sozinho</span>
    </div>
  </section>

  <section id=\"features\" class=\"wrap\">
    <div class=\"section-head\">
      <span class=\"kicker\">As três fontes</span>
      <h2 class=\"section-title\">Um lugar só pra decidir onde ouvir</h2>
      <p class=\"section-sub\">Cada faixa buscada mostra o que cada plataforma tem: preço de loja, opção \"pague o quanto quiser\" e a melhor fonte grátis via rede P2P.</p>
    </div>
    <div class=\"features\">
      <div class=\"feature beatport\">
        <div class=\"feature-icon\">
          <svg width=\"20\" height=\"20\" viewBox=\"0 0 24 24\" fill=\"none\"><path d=\"M4 19V7m5 12V4m5 15v-9m5 9V9\" stroke=\"currentColor\" stroke-width=\"2.2\" stroke-linecap=\"round\"/></svg>
        </div>
        <h3>Beatport</h3>
        <p>Preço, BPM, key e label direto da página oficial — pra quem compra faixa por faixa.</p>
      </div>
      <div class=\"feature bandcamp\">
        <div class=\"feature-icon\">
          <svg width=\"20\" height=\"20\" viewBox=\"0 0 24 24\" fill=\"none\"><path d=\"M4 17h6l8-10H12L4 17Z\" stroke=\"currentColor\" stroke-width=\"2.2\" stroke-linejoin=\"round\"/></svg>
        </div>
        <h3>Bandcamp</h3>
        <p>Preço real, \"pague o quanto quiser\" ou grátis — direto do catálogo independente.</p>
      </div>
      <div class=\"feature free\">
        <div class=\"feature-icon\">
          <svg width=\"20\" height=\"20\" viewBox=\"0 0 24 24\" fill=\"none\"><path d=\"M12 3v12m0 0 4-4m-4 4-4-4M5 21h14\" stroke=\"currentColor\" stroke-width=\"2.2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/></svg>
        </div>
        <h3>Soulseek <span style=\"font-weight:500;color:var(--muted);font-size:12.5px\">(no app de mesa)</span></h3>
        <p>Busca na rede P2P e traz a melhor fonte grátis pra baixar, com fallback automático.</p>
      </div>
    </div>
  </section>

  <section class=\"wrap\">
    <div class=\"section-head\">
      <span class=\"kicker\">Como funciona</span>
      <h2 class=\"section-title\">Três passos, sem enrolação</h2>
    </div>
    <div class=\"steps\">
      <div class=\"step\">
        <span class=\"num\">1</span>
        <h3>Busque a faixa</h3>
        <p>Artista + nome da música já resolve — quanto mais específico, melhor o resultado.</p>
      </div>
      <div class=\"step\">
        <span class=\"num\">2</span>
        <h3>Compare</h3>
        <p>Veja preço no Beatport, no Bandcamp e, no app, a melhor fonte grátis no Soulseek.</p>
      </div>
      <div class=\"step\">
        <span class=\"num\">3</span>
        <h3>Decida onde ouvir</h3>
        <p>Abra a loja pra fechar a compra, ou baixe pelo app — sem gambiarra de \"botão de comprar\".</p>
      </div>
    </div>
  </section>

  <section id=\"demo\" class=\"wrap\">
    <div class=\"section-head\">
      <span class=\"kicker\">Testar agora</span>
      <h2 class=\"section-title\">Sem instalar nada</h2>
      <p class=\"section-sub\">Essa busca roda direto no navegador — Beatport e Bandcamp em tempo real. Pra Soulseek, use o app de mesa.</p>
    </div>
    <div class=\"demo-card\">
      <form id=\"searchForm\" class=\"demo-form\">
        <input id=\"query\" name=\"q\" placeholder=\"Ex.: Blue Monday\" autocomplete=\"off\" required />
        <button type=\"submit\">
          <svg width=\"16\" height=\"16\" viewBox=\"0 0 24 24\" fill=\"none\"><circle cx=\"11\" cy=\"11\" r=\"7\" stroke=\"currentColor\" stroke-width=\"2.2\"/><path d=\"m20 20-3.5-3.5\" stroke=\"currentColor\" stroke-width=\"2.2\" stroke-linecap=\"round\"/></svg>
          Buscar
        </button>
      </form>
      <div id=\"status\" class=\"demo-status\"></div>
      <div id=\"results\" class=\"demo-results\"></div>
    </div>
  </section>

  <section id=\"download\" class=\"wrap\">
    <div class=\"download-panel\">
      <h2>Leve o app pra sua mesa</h2>
      <p>Mac ou Windows — o instalador é sempre o build mais recente. Você atualiza o código aqui, todo mundo que já baixou pega a versão nova no próximo download.</p>
      <div class=\"download-ctas\">
        <a class=\"btn btn-mac\" href=\"__MAC_DOWNLOAD_URL__\">
          <svg width=\"18\" height=\"18\" viewBox=\"0 0 24 24\" fill=\"currentColor\"><path d=\"M16.365 1.43c0 1.14-.47 2.11-1.19 2.86-.79.83-2.03 1.45-3.05 1.37-.13-1.09.43-2.24 1.15-2.96.78-.81 2.13-1.42 3.09-1.27ZM20.9 17.4c-.52 1.2-.76 1.74-1.42 2.8-.92 1.48-2.22 3.33-3.83 3.35-1.43.02-1.8-.93-3.74-.92-1.94.01-2.35.94-3.78.92-1.6-.02-2.83-1.68-3.75-3.16-2.57-4.1-2.84-8.9-1.25-11.46 1.13-1.82 2.9-2.89 4.57-2.89 1.7 0 2.77 1 4.18 1 1.36 0 2.2-1 4.18-1 1.49 0 3.07.81 4.19 2.21-3.69 2.02-3.09 7.28.65 9.15Z\"/></svg>
          Baixar .dmg
        </a>
        <a class=\"btn btn-win\" href=\"__WINDOWS_DOWNLOAD_URL__\">
          <svg width=\"17\" height=\"17\" viewBox=\"0 0 24 24\" fill=\"currentColor\"><path d=\"M3 5.5 10.4 4.5V11.4H3V5.5ZM11.3 4.35 21 3V11.3H11.3V4.35ZM3 12.4H10.4V19.4L3 18.4V12.4ZM11.3 12.4H21V20.9L11.3 19.6V12.4Z\"/></svg>
          Baixar .zip
        </a>
      </div>
      <p class=\"download-foot\">Build automático via GitHub Actions a cada atualização do código-fonte.</p>
    </div>
  </section>

  <section id=\"faq\" class=\"wrap\">
    <div class=\"section-head\">
      <span class=\"kicker\">Perguntas</span>
      <h2 class=\"section-title\">O que você precisa saber</h2>
    </div>
    <div class=\"faq-list\">
      <details class=\"faq-item\">
        <summary>O app é gratuito?<svg width=\"18\" height=\"18\" viewBox=\"0 0 24 24\" fill=\"none\"><path d=\"m6 9 6 6 6-6\" stroke=\"currentColor\" stroke-width=\"2.2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/></svg></summary>
        <div class=\"faq-body\">Sim, sem custo e sem cadastro pra buscar preço. O Soulseek pede uma conta própria (criada na hora, sem site nenhum) só porque é assim que a rede P2P funciona.</div>
      </details>
      <details class=\"faq-item\">
        <summary>Por que o Mac avisa que o app não é confiável?<svg width=\"18\" height=\"18\" viewBox=\"0 0 24 24\" fill=\"none\"><path d=\"m6 9 6 6 6-6\" stroke=\"currentColor\" stroke-width=\"2.2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/></svg></summary>
        <div class=\"faq-body\">O app ainda não tem assinatura paga da Apple. Na primeira abertura, clique com o botão direito no ícone → \"Abrir\" → confirme. Depois disso abre normalmente, com duplo clique.</div>
      </details>
      <details class=\"faq-item\">
        <summary>O link de download muda a cada atualização?<svg width=\"18\" height=\"18\" viewBox=\"0 0 24 24\" fill=\"none\"><path d=\"m6 9 6 6 6-6\" stroke=\"currentColor\" stroke-width=\"2.2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/></svg></summary>
        <div class=\"faq-body\">Não. O link é fixo e sempre aponta pro build mais recente — cada novo push já deixa o download atualizado, sem precisar avisar ninguém.</div>
      </details>
      <details class=\"faq-item\">
        <summary>Beatport e Bandcamp têm link cruzado entre si?<svg width=\"18\" height=\"18\" viewBox=\"0 0 24 24\" fill=\"none\"><path d=\"m6 9 6 6 6-6\" stroke=\"currentColor\" stroke-width=\"2.2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/></svg></summary>
        <div class=\"faq-body\">Não — cada busca é independente, então o \"melhor resultado\" de cada loja pode ser uma versão ou remix diferente da mesma música. Sempre confira antes de comprar.</div>
      </details>
    </div>
  </section>

</main>

<footer>
  <div class=\"wrap footer-row\">
    <span>Music Aggregator — feito pra achar onde ouvir, não pra vender nada.</span>
    <a href=\"#top\">Voltar ao topo ↑</a>
  </div>
</footer>

<script>
  const form = document.getElementById('searchForm');
  const query = document.getElementById('query');
  const status = document.getElementById('status');
  const results = document.getElementById('results');
  const submitBtn = form.querySelector('button');

  function escapeHtml(str) {
    return String(str ?? '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function rowHtml(item, tag) {
    const cover = item.cover
      ? `<img class=\"result-cover\" src=\"${escapeHtml(item.cover)}\" alt=\"\" loading=\"lazy\" />`
      : `<div class=\"result-cover\"></div>`;
    return `<a class=\"result-row\" href=\"${escapeHtml(item.url)}\" target=\"_blank\" rel=\"noopener\">
      ${cover}
      <div class=\"result-info\">
        <div class=\"result-title\">${escapeHtml(item.title)}</div>
        <div class=\"result-artist\">${escapeHtml(item.artist)}</div>
      </div>
      <span class=\"result-tag ${tag}\">${tag}</span>
      <span class=\"result-price\">${escapeHtml(item.price)}</span>
    </a>`;
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const value = query.value.trim();
    if (!value) return;

    submitBtn.disabled = true;
    status.textContent = 'Buscando...';
    results.innerHTML = '';

    try {
      const response = await fetch(`/api/search?q=${encodeURIComponent(value)}`);
      const data = await response.json();

      if (data.error) {
        status.textContent = data.error;
        return;
      }

      const total = data.beatport.length + data.bandcamp.length;
      status.textContent = total
        ? `${data.beatport.length} resultado(s) no Beatport · ${data.bandcamp.length} no Bandcamp`
        : 'Nada encontrado — tente ser mais específico (artista + faixa).';

      const items = [
        ...data.beatport.map((item) => rowHtml(item, 'beatport')),
        ...data.bandcamp.map((item) => rowHtml(item, 'bandcamp')),
      ];
      results.innerHTML = items.join('') || `<div class=\"empty-hint\">Sem resultados por aqui ainda.</div>`;
    } catch (err) {
      status.textContent = 'Deu ruim na busca — tenta de novo em alguns segundos.';
    } finally {
      submitBtn.disabled = false;
    }
  });
</script>
</body>
</html>
""".replace("__MAC_DOWNLOAD_URL__", MAC_DOWNLOAD_URL).replace("__WINDOWS_DOWNLOAD_URL__", WINDOWS_DOWNLOAD_URL)


def fetch_sources(query: str):
    beatport_results = search_beatport(query, limit=4)
    bandcamp_results = search_bandcamp(query, limit=4)

    beatport_payload = [
        {
            "title": item.full_title,
            "artist": item.artist_label,
            "price": item.price_display or "—",
            "url": item.url,
            "cover": item.cover_url,
            "label": item.label,
            "bpm": item.bpm,
            "key": item.key,
        }
        for item in beatport_results
    ]

    bandcamp_payload = [
        {
            "title": item.title,
            "artist": item.artist,
            "price": item.price_label,
            "url": f"https://bandcamp.com{item.url}" if not item.url.startswith("http") else item.url,
            "cover": item.cover_url,
            "type": item.item_type,
        }
        for item in bandcamp_results
    ]

    return beatport_payload, bandcamp_payload


@app.get("/")
def home():
    return HTML_PAGE


@app.get("/api/search")
def search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"error": "Informe uma query"}), 400

    beatport, bandcamp = fetch_sources(query)
    return jsonify({
        "query": query,
        "beatport": beatport,
        "bandcamp": bandcamp,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
