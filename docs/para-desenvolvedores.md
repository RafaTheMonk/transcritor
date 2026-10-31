# Para desenvolvedores

Como o Transcritor funciona por dentro. Para instalar e usar, veja o [README](../README.md).

## Preparar o ambiente

```bash
bash instalar.sh --dev            # inclui o pytest
bash instalar.sh --dev --sem-gpu  # sem as bibliotecas da NVIDIA (cerca de 1,8 GB)
```

O script confere `ffmpeg`, `ffprobe` e Python, cria a `.venv`, instala as dependências e cria os comandos em `~/.local/bin` e o atalho em `~/.local/share/applications/transcritor.desktop`. O atalho não tem `MimeType`, então o app não vira padrão para abrir vídeo. Pode ser rodado de novo sem problema.

Versões fixadas em `requirements.txt`: a versão 19 do `av` quebra o faster-whisper 1.2.1 (`metadata_errors`), e as bibliotecas CUDA dependem da versão do `ctranslate2`.

## Testes

```bash
.venv/bin/python -m pytest          # rápidos, com ffmpeg real e motor falso
.venv/bin/python -m pytest -m lento # só o teste lento, com o modelo small real em CPU
```

Os testes da interface rodam com `QT_QPA_PLATFORM=offscreen` (já definido por eles) e uma CLI falsa em `tests/cli_falsa.py`. Para gerar capturas de tela, defina `TRANSCRITOR_PNG_DIR=/pasta`. O GitHub Actions roda a suíte e uma transcrição real do áudio de exemplo em Ubuntu, a cada mudança e uma vez por semana, para pegar dependência nova que quebre.

## Estrutura

| Caminho | O que é |
|---|---|
| `bin/transcrever` | lançador da CLI, único lugar que define `LD_LIBRARY_PATH` |
| `bin/transcritor` | lançador da janela |
| `transcritor/midia.py` | análise com ffprobe e extração com ffmpeg |
| `transcritor/gpu.py` | escolha de dispositivo e modelos padrão |
| `transcritor/motor.py` | transcrição com faster-whisper |
| `transcritor/exportar.py` | txt, srt, vtt e json |
| `transcritor/pipeline.py` | orquestra as etapas e emite os eventos |
| `transcritor/cancelamento.py` | sinais, temporários e saída forçada |
| `transcritor/cli.py` | linha de comando |
| `transcritor/gui.py` | janela em PySide6 |

**Decisão central:** a janela nunca carrega o modelo. Ela roda `bin/transcrever --json-eventos` com QProcess, um processo por arquivo, e lê eventos NDJSON. Por isso cancelar mata o processo de verdade, a memória da GPU volta ao sair e uma falha nativa da placa não derruba a janela.

## Linha de comando

```bash
transcrever video.mov
transcrever aula1.mp4 entrevista.mp3 --idioma auto
transcrever reuniao.m4a --modelo medium --formatos txt,srt --saida ~/Documentos/reunioes
```

| Opção | Padrão | O que faz |
|---|---|---|
| `--modelo` | `auto` | `auto`, `small`, `medium`, `large-v3-turbo`, `large-v3` |
| `--idioma` | `pt` | código (`pt`, `en`, `es`...) ou `auto` para detectar |
| `--dispositivo` | `auto` | `auto`, `cuda` ou `cpu` |
| `--saida` | `Documentos/Transcricoes` | pasta onde ficam as transcrições |
| `--faixa N` | a marcada como padrão | faixa de áudio, contando de 0 |
| `--formatos` | `txt,srt,vtt,json` | lista separada por vírgula |
| `--sem-salvar-audio` | desligado | não guarda o áudio extraído do vídeo |
| `--json-eventos` | desligado | eventos NDJSON em stdout, nada mais em stdout |

Código de saída: `0` tudo certo, `1` algum arquivo falhou (os demais continuam), `130` cancelado, `2` uso errado.

Vídeo ou áudio é decidido pelo conteúdo (ffprobe), nunca pela extensão. Um mp3 com capa conta como áudio. Áudio puro é transcrito direto, sem extrair nem copiar. Em vídeo, o áudio é copiado sem reencodar quando o codec cabe num contêiner comum (aac e alac em `.m4a`, mp3, opus, vorbis, flac, pcm em `.wav`). Se a cópia falhar ou o codec não couber, reencoda para aac 192k em `.m4a`.

## Saídas

Para cada arquivo, uma pasta `<saida>/<nome sem extensão>/` com `<nome>.txt`, `.srt`, `.vtt`, `.json` e o áudio extraído (só quando a entrada era vídeo). Se a pasta já existir, vira `-2`, `-3`, sem sobrescrever. Nome de arquivo muito longo é cortado em 200 bytes.

Tudo é gravado numa pasta temporária oculta (`.transcritor-tmp-*`, só para o dono) e renomeado só no fim, então cancelar nunca deixa saída pela metade. Se o processo levar SIGKILL ou faltar energia, essa pasta oculta fica para trás, e a próxima rodada com a mesma saída apaga. Uma rodada ainda viva não perde a dela, porque segura uma trava.

- TXT: uma linha por segmento, sem tempo.
- SRT: `HH:MM:SS,mmm`. VTT: `HH:MM:SS.mmm` com cabeçalho `WEBVTT`.
- JSON: metadados (arquivo de origem, modelo, dispositivo, idioma, duração) e os segmentos.
- Áudio sem fala: termina com sucesso, avisa e não grava nada (`vazio` verdadeiro).

## Modelos e GPU

`auto` usa `large-v3-turbo`: em `float16` na GPU e em `int8` sem GPU. Os padrões estão em constantes no topo de `transcritor/gpu.py`.

Medido com RTX 3050 6GB e Intel Core i5-13420H, em um único vídeo de 317,6 s em português, com uma voz e muitos nomes próprios. Tempo total da rodada (varia uns 2 s entre rodadas) e pico de memória da placa:

| Modelo | Dispositivo | Tipo | Tempo | VRAM | Qualidade neste vídeo |
|---|---|---|---|---|---|
| `large-v3-turbo` | GPU | `float16` (padrão) | 12,6 s | 2323 MiB | a melhor, com menos erros em nomes próprios |
| `large-v3-turbo` | GPU | `int8_float16` | 11,9 s | 1331 MiB | igual, salvo um nome |
| `large-v3-turbo` | CPU | `int8` (padrão sem GPU) | 61,5 s | 0 | quase igual à da GPU |
| `large-v3` | GPU | `float16` | 33,9 s | 4147 MiB | não superou o turbo: acerta alguns nomes e erra outros |
| `large-v3` | GPU | `int8_float16` | 27,9 s | 2291 MiB | como a anterior, com um erro a mais |
| `small` | GPU | `float16` | 9,1 s | 883 MiB | bem pior, erra vários nomes e junta frases |
| `small` | CPU | `int8` | 33,6 s | 0 | bem pior, como a de cima |

- GPU disponível significa: há dispositivo CUDA e a `libcublas.so.12` carrega. As bibliotecas CUDA vêm de pacotes pip dentro da `.venv`, e só o lançador `bin/transcrever` as coloca no `LD_LIBRARY_PATH`. Por isso use sempre o `transcrever` e não `python -m transcritor.cli` solto.
- Fallback: se a GPU falhar (biblioteca ausente, falta de memória, erro de CUDA), no carregamento ou no meio da transcrição, o parcial é descartado, sai um aviso e tudo refaz em CPU. Mantém o modelo, em `int8`. Com `--dispositivo cuda` explícito não há fallback: dá erro. Testado com falta de memória de verdade, com outro processo segurando a placa.
- Os modelos ficam no cache do Hugging Face. O app tenta o cache primeiro e só baixa se faltar (etapa `baixando_modelo`).
- Com o modelo em cache não há rede: conferido em rodada real sem nenhuma conexão IP e também com a rede desligada (`unshare -rn`). A única escrita fora da pasta de saída é do `onnxruntime` (usado para detectar fala), que guarda um identificador local em `~/.cache/Microsoft/DeveloperTools/.onnxruntime/`, sem enviar nada nessas rodadas.

## Interface gráfica

```bash
transcritor                       # abre a janela
transcritor video.mov aula.mp3    # abre já com arquivos na fila
```

- **Entrada:** solte arquivos na janela ou use "Escolher arquivos" (Ctrl+O). O diálogo filtra vídeo e áudio, inclusive extensões em maiúsculas, e tem a opção "Todos os arquivos", porque quem decide o tipo é o ffprobe.
- **Opções:** Qualidade (Automática, Rápida = small, Equilibrada = large-v3-turbo, Máxima = large-v3), Idioma, Dispositivo e a pasta de saída. As escolhas ficam guardadas (QSettings) e valem a partir do próximo arquivo da fila.
- **Cancelar:** pede ao processo que encerre (SIGTERM) e força o fim após 5 segundos. Se houver mais arquivos na fila, a janela pergunta se é só o atual ou tudo. Fechar a janela com trabalho em andamento pede confirmação e encerra o processo filho.
- **Falhas:** linhas que não forem JSON são ignoradas e, se o processo morrer sem o evento `fim`, o arquivo vira Erro com o código de saída e o final do stderr. Se a própria janela levar SIGKILL, o filho percebe (`PR_SET_PDEATHSIG`), cancela, mata o ffmpeg e limpa.
- **Testes:** para usar outra CLI, defina `TRANSCRITOR_CLI`.

## Contrato de eventos (`--json-eventos`)

Um objeto JSON por linha, com `flush` a cada linha. Bibliotecas e o ffmpeg não conseguem escrever em stdout: o descritor 1 é redirecionado para o stderr. Todos têm `"arquivo"` (caminho absoluto) quando há arquivo em andamento.

```
{"tipo":"etapa","etapa":"analisando|extraindo|carregando_modelo|baixando_modelo|transcrevendo|salvando"}
{"tipo":"midia","eh_video":bool,"duracao":float,"codec_audio":str,"canais":int,"faixas_audio":int}
{"tipo":"dispositivo","dispositivo":"cuda|cpu","compute_type":str,"modelo":str}
{"tipo":"idioma","idioma":str,"probabilidade":float}
{"tipo":"aviso","mensagem":str}
{"tipo":"segmento","inicio":float,"fim":float,"texto":str,"progresso":0 a 1}
{"tipo":"fim","pasta":str,"arquivos":{"txt":str,...,"audio":str},"duracao_audio":float,"tempo_processamento":float,"vazio":bool}
{"tipo":"erro","codigo":str,"mensagem":str}
```

- Um `dispositivo` que chega de novo no meio do mesmo arquivo significa que a GPU falhou: descarte os segmentos recebidos, a transcrição recomeçou em CPU.
- `fim` com `vazio` verdadeiro tem `pasta` vazia e `arquivos` vazio.
- Cancelar: mande SIGTERM (ou SIGINT) ao processo. Ele encerra o ffmpeg, apaga os temporários, emite `erro` com código `cancelado` e sai com 130. Se estiver preso em código nativo, uma thread vigia faz o mesmo em até 5 segundos. Um segundo sinal encurta a espera.
- Quem lê os eventos morreu: com `--json-eventos`, a morte do pai (mesmo por SIGKILL) vale como SIGTERM, e pipe fechado encerra com 141, limpando os temporários. Lance a CLI da thread principal, porque o kernel olha a thread que criou o processo. O ffmpeg morre sozinho se a própria CLI levar SIGKILL.
- Códigos de erro: `arquivo_nao_encontrado`, `nao_e_arquivo`, `sem_permissao`, `arquivo_vazio`, `saida_invalida`, `midia_invalida`, `sem_audio`, `faixa_invalida`, `extracao_falhou`, `extracao_divergente`, `ffmpeg_ausente`, `gpu_indisponivel`, `gpu_falhou`, `modelo_invalido`, `modelo_indisponivel`, `cancelado`, `erro_inesperado`.

## Depurar

- **Erro inesperado sem detalhe:** rode com `TRANSCRITOR_DEBUG=1` para ver o traceback no stderr.
- **`TypeError ... metadata_errors`:** o `av` está na versão errada. Rode `bash instalar.sh` para voltar à versão fixada.
- **Falta de memória na GPU:** feche outros programas que usam a placa ou use `--modelo small`. O fallback para CPU já acontece sozinho com `--dispositivo auto`.
