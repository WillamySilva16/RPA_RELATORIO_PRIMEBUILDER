# Robô PrimeBuilder → Google Sheets

Automação para:

1. Abrir o PrimeBuilder no Chrome.
2. Fazer login.
3. Abrir "Visões de Dados".
4. Usar o modelo "Registro de Visita Supervisão".
5. Perguntar data inicial e data final.
6. Preencher "Data de Agendamento".
7. Clicar em "Exportar - Fases por Coluna".
8. Aguardar o CSV.
9. Ler o CSV.
10. Fazer upsert no Google Sheets usando "Código da OS" como chave.

## 1. Instalação

Recomendado: Python 3.10+.

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

Instale:

```bash
pip install -r requirements.txt
```

O Selenium 4 usa o Selenium Manager para localizar o ChromeDriver automaticamente.

## 2. Configuração

Copie:

```text
.env.example
```

para:

```text
.env
```

Preencha a senha do PrimeBuilder no `.env`.

Coloque a Service Account em:

```text
credentials/service-account.json
```

NÃO coloque esse arquivo no Git.

A planilha precisa estar compartilhada com o e-mail da Service Account como Editor.

## 3. Executar

```bash
python main.py
```

O programa pergunta:

```text
Data de agendamento - Inicial (DD/MM/AAAA):
Data de agendamento - Final   (DD/MM/AAAA):
```

Exemplo:

```text
Data de agendamento - Inicial (DD/MM/AAAA): 01/09/2026
Data de agendamento - Final   (DD/MM/AAAA): 07/09/2026
```

## 4. Atualização do Sheets

A coluna:

```text
Código da OS
```

é usada como chave.

Se a OS já existir na planilha, a linha é atualizada.

Se não existir, uma nova linha é inserida.

Isso evita duplicação quando o mesmo período for exportado novamente.

## 5. Segurança

Nunca publique:

- `.env`
- `credentials/service-account.json`

A chave privada da Service Account deve ser mantida fora do GitHub.
