# Como contribuir

Obrigado pelo interesse! Toda ajuda conta, inclusive sem programar.

## Sem programar

- **Testou em outra distribuição?** Conte como foi em uma [issue](https://github.com/RafaTheMonk/transcritor/issues/new/choose), mesmo que tenha dado tudo certo.
- **Achou um passo confuso no README?** Isso também é um problema que vale relatar.
- **Encontrou um erro?** Use o modelo de relato de problema. Não anexe áudio nem transcrição com conteúdo privado, as issues são públicas.

## Programando

```bash
git clone https://github.com/RafaTheMonk/transcritor.git
cd transcritor
bash instalar.sh --dev
.venv/bin/python -m pytest
```

A arquitetura e o contrato de eventos estão em [docs/para-desenvolvedores.md](docs/para-desenvolvedores.md).

Antes de abrir um pull request:

1. Rode os testes e inclua um teste novo quando corrigir um defeito.
2. Mantenha cada commit em uma única mudança, no formato `tipo: descrição` (`feat`, `fix`, `docs`, `test`, `ci`, `chore`).
3. Escreva mensagens, textos da interface e documentação em português do Brasil.

## Princípios do projeto

- **O áudio nunca sai da máquina.** Nada de serviço externo nem telemetria. A rede só é usada para baixar modelos.
- **Quem usa pode não saber programar.** Mensagem de erro em português, dizendo o que fazer, sem traceback.
- **A janela não carrega o modelo.** Ela conversa com a CLI por eventos, em outro processo.
