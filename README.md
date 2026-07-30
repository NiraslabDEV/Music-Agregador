# Music Aggregator

Busca uma faixa e mostra, lado a lado:
- **Beatport** — preço, BPM, key, label.
- **Bandcamp** — preço (ou "pague o quanto quiser" / grátis).
- **Soulseek** — a melhor fonte grátis pra baixar, com fallback automático de fonte.

Feito pra separar do PopBalloon Archiver: aquele é sobre baixar transcrição/áudio
de podcast; este é sobre montar um set (preço + onde comprar + opção grátis).

## Como rodar localmente

```powershell
.venv\Scripts\python.exe main.py
```

Se ainda não tem o venv:

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Versão web online

A pasta api agora expõe uma interface simples para uso na Vercel. Para publicar:

1. Crie um repositório no GitHub.
2. Envie o projeto com:
   ```powershell
   git init
   git add .
   git commit -m "Primeiro deploy"
   git branch -M main
   git remote add origin <URL_DO_REPOSITORIO>
   git push -u origin main
   ```
3. No Vercel, importe o repositório e confirme o deploy.
4. A aplicação ficará disponível em uma URL do Vercel.
5. Se quiser, adicione uma variável de ambiente opcional `PORT` para testes locais; o deploy não precisa dela.

## App instalável (Mac e Windows) com atualização automática

Toda vez que um push cai na branch `main` (mudando `core/`, `ui/`, `main.py` etc.),
o workflow `.github/workflows/build-desktop.yml` builda o app nativo pro Mac
(`.dmg`) e pro Windows (`.zip`) via GitHub Actions e publica os dois num
release do GitHub chamado sempre `latest` — o release é **substituído**, não
acumulado, então o link de download nunca muda:

```
https://github.com/<usuario>/<repo>/releases/latest/download/MusicAggregator-macOS.dmg
https://github.com/<usuario>/<repo>/releases/latest/download/MusicAggregator-Windows.zip
```

A página web (`api/index.py`) já tem os botões de download apontando pra essas
URLs — basta ajustar a constante `GITHUB_REPO` no topo do arquivo pro
`usuario/repo` real assim que o repositório existir no GitHub.

Pré-requisitos únicos (feitos uma vez):
1. Criar o repositório no GitHub e dar `git push` (o workflow já está pronto).
2. Nenhuma conta paga é necessária — o app builda, mas não é assinado pela
   Apple. Na primeira abertura no Mac, o usuário precisa clicar com o botão
   direito no app → "Abrir" (o Gatekeeper bloqueia apps não assinados por
   padrão). Assinatura oficial da Apple (opcional, remove esse aviso) exige
   uma conta Apple Developer (US$99/ano).

Dependências do app de mesa (Soulseek incluso) ficam em
`requirements-desktop.txt`, separadas do `requirements.txt` usado pela Vercel
(que só precisa de Flask, sem Flet/Flutter).

## Como funciona cada fonte

**Beatport** (`core/beatport.py`) — sem autenticação. A própria página de busca
carrega um JSON completo (`__NEXT_DATA__`) com tudo: preço, BPM, key, label,
gênero. Usa `requests` normal — o Beatport não bloqueia.

**Bandcamp** (`core/bandcamp.py`) — sem autenticação, mas **via `curl.exe` do
sistema** em vez de uma lib Python. O Bandcamp bloqueia por fingerprint de
TLS/HTTP: `requests` e até `curl_cffi` (que imita navegador) foram testados e
falharam nas páginas de faixa/álbum; só o `curl.exe` real do Windows passou de
forma consistente. Isso significa que o Windows precisa ter `curl.exe`
disponível no PATH — já vem de fábrica desde o Windows 10 versão 1803 (2018).
Se por algum motivo não existir, a busca do Bandcamp simplesmente não retorna
nada (nunca derruba o app).

**Soulseek** (`core/soulseek.py`) — mesmo motor testado e usado no PopBalloon
Archiver (copiado, com pasta de configuração própria em
`%APPDATA%\MusicAggregator\`, pra não colidir com o outro app). Rede P2P real:
precisa de uma conta (criada no primeiro login, sem cadastro em site nenhum) e
de compartilhar alguma pasta pra não ser tratado como leecher.

## Limitações conhecidas

- **Bandcamp não tem link direto pro Beatport nem vice-versa** — a busca em
  cada plataforma é independente, então o "melhor resultado" de cada uma pode
  ser uma versão/remix diferente da mesma música. Sempre confira antes de
  comprar.
- A busca no Bandcamp é por texto livre e o catálogo é gigante e não curado —
  para faixas populares, pode aparecer capa/remix de banda cover em vez do
  original. Fica melhor quanto mais específica a busca (artista + faixa).
- Scraping de terceiro: se Beatport/Bandcamp mudarem a estrutura da página,
  os módulos podem parar de achar preço — tudo foi escrito pra falhar em
  silêncio (mostra "não encontrado" em vez de quebrar o app).
- Nenhum "botão de comprar" de verdade — os links levam pro site oficial pra
  você fechar a compra lá. Não haveria como automatizar isso sem violar os
  termos de uso das lojas.
