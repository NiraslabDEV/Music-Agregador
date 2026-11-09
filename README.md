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

## Busca por produtor (app desktop)

Selecione **Produtor** no tipo de busca e digite o nome artístico completo.
O app reúne faixas creditadas ao nome no Beatport e no Bandcamp, incluindo
colaborações e faixas de até seis álbuns encontrados no Bandcamp. Use o filtro
de texto e a seleção de fonte para explorar a lista; prévias e downloads
continuam disponíveis nos cards.

O filtro exige o crédito completo do artista, não uma menção no título.
Identificadores distintos com o mesmo nome no Beatport são tratados como
ambiguidade e omitidos. Isso reduz resultados incorretos, mas nomes homônimos
no Bandcamp ainda exigem conferir a página do artista. A lista é uma seleção
dos resultados públicos, não uma discografia completa.

O Soulseek faz uma busca pelo produtor e associa arquivos com artista e título
correspondentes no nome do arquivo, preservando o nome da versão/remix.
Arquivos sem essa evidência são omitidos. Esses nomes são fornecidos por usuários:
a associação não certifica autoria ou conteúdo do áudio. É necessário estar
conectado ao Soulseek. A busca por produtor está no programa desktop; a página
web continua oferecendo a busca simples das lojas e o download do programa.

## Baixar do YouTube (app desktop)

O botão **YouTube** em cada faixa abre a lista de vídeos da busca (sem chave de
API — usa o `yt-dlp`). Você escolhe o vídeo certo (dá pra abrir no navegador e
ouvir antes) e clica em **Baixar áudio**. O download aparece na fila de
Downloads e, quando termina, entra na Biblioteca com um selo vermelho do
YouTube (com BPM/tom, player e crates, igual às outras).

Os arquivos vão para uma pasta **separada** da do Soulseek — por padrão
`~/Music/YouTube`, dá pra trocar no próprio diálogo. Separada de propósito: a
qualidade é a do YouTube (AAC ~128 kbps, não é FLAC) e a pasta do Soulseek é
compartilhada na rede, essa não.

Com `ffmpeg` no PATH o áudio sai sempre em `.m4a` (vídeos que só têm Opus são
convertidos) e a análise de BPM/tom consegue ler `.m4a`. Sem ffmpeg, o app
baixa o `.m4a` que o YouTube já oferece, mas a análise de BPM/tom desses
arquivos falha (dá pra corrigir à mão em "Corrigir BPM/tom").

## Favoritas (app desktop)

A estrela em cada faixa guarda ela na aba **Favoritas** — mesmo que o Soulseek
não tenha a faixa hoje. Também dá pra adicionar uma favorita direto, sem buscar
("Adicionar"). Cada favorita mostra o resultado da última verificação: sem
fonte no Soulseek, com fonte (quantas e a melhor qualidade) ou já na
biblioteca.

- **⟳ Verificar** busca de novo nas três fontes; se o Soulseek tiver a faixa,
  os botões de download (FLAC/WAV/MP3 e "ver todos") aparecem na própria linha.
- **Verificar todas** passa por todas que ainda não estão na biblioteca, uma
  de cada vez, e avisa quais apareceram no Soulseek.
- Toda busca normal em Descobrir de uma faixa favoritada também atualiza a
  favorita.

Só o resumo da verificação fica salvo; as fontes do Soulseek não (na rede P2P,
quem tinha a faixa ontem pode estar offline hoje) — por isso o botão de baixar
pede uma verificação nova.

## Links do Spotify (app desktop)

Cole no campo de busca o link de uma **música, álbum ou playlist** do Spotify
(sozinho ou junto com outras faixas/tracklist). O app lê os nomes e artistas e
busca cada faixa no Beatport, Bandcamp e Soulseek, como uma lista colada. Os
cards que vieram do Spotify ganham a prévia de 30 s e um atalho pra abrir a
faixa lá.

Não precisa de conta de desenvolvedor: o app lê a página de "embed" (o player
que o Spotify deixa qualquer site incorporar). Por isso playlists muito grandes
podem vir cortadas, e playlists privadas não abrem.

O Spotify é só referência — **o áudio de lá não é baixado**: ele é protegido
por DRM. Pra ter o arquivo, use o Soulseek, a loja ou o botão YouTube do card.
