"""Erro de dominio com codigo estavel, para a CLI e a interface mostrarem sem traceback."""


class ErroTranscritor(Exception):
    def __init__(self, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem
