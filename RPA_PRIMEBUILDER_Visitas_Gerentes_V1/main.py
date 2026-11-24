import os
import re
import time
from pathlib import Path
from datetime import datetime

import gspread
import pandas as pd
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"

import pb_novo_layout as pbnl  # adaptações ao layout novo do PrimeBuilder
MODELO_RELATORIO = "RS - Gerente"
LOG_DIR = BASE_DIR / "logs"
CREDENTIALS_FILE = BASE_DIR / "credentials" / "service-account.json"

load_dotenv(BASE_DIR / ".env")

PRIME_LOGIN_URL = os.getenv(
    "PRIME_LOGIN_URL",
    "https://www.primebuilder.com.br/Frontend/Default/Login",
)
PRIME_REPORT_URL = os.getenv(
    "PRIME_REPORT_URL",
    "https://www.primebuilder.com.br/Frontend/TransitionsLayoutReport/",
)
PRIME_EMPRESA = os.getenv("PRIME_EMPRESA", "3073")
PRIME_USUARIO = os.getenv("PRIME_USUARIO", "rs")
PRIME_SENHA = os.getenv("PRIME_SENHA", "")

SHEET_ID = os.getenv(
    "GOOGLE_SHEET_ID",
    "1mekHMFjj4CM4WjYH7XA2s6R_kY572mN8Z2Obgq7B-Zs",
)
WORKSHEET_NAME = os.getenv("GOOGLE_WORKSHEET_NAME", "")
UPSERT_KEY = os.getenv("UPSERT_KEY", "Código da OS")


def log(msg, level="INFO"):
    """Registra a mensagem no console e em um arquivo diário.

    O console é mantido para testes manuais. Quando o robô roda com
    pythonw.exe, stdout pode não existir; nesse caso o log continua
    sendo gravado normalmente no arquivo.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    mensagem = f"[{timestamp}] [{level.upper()}] {msg}"
    log_file = LOG_DIR / f"{now:%Y-%m-%d}.log"

    try:
        with log_file.open("a", encoding="utf-8") as f:
            f.write(mensagem + "\n")
    except Exception:
        # Nunca deixa uma falha no arquivo de log derrubar o robô.
        pass

    try:
        print(mensagem, flush=True)
    except Exception:
        # pythonw.exe normalmente não possui console/stdout.
        pass


def log_exception(context, exc):
    """Registra a exceção e o traceback completo no log diário."""
    import traceback

    log(f"{context}: {type(exc).__name__}: {exc}", level="ERROR")

    traceback_text = traceback.format_exc()
    for line in traceback_text.rstrip().splitlines():
        log(line, level="ERROR")


def normalize_date(value):
    value = value.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).strftime("%d/%m/%Y")
        except ValueError:
            pass
    raise ValueError("Data inválida. Use DD/MM/AAAA.")


def ask_dates():
    while True:
        try:
            inicial = normalize_date(input("Data de agendamento - Inicial (DD/MM/AAAA): "))
            final = normalize_date(input("Data de agendamento - Final   (DD/MM/AAAA): "))

            d1 = datetime.strptime(inicial, "%d/%m/%Y")
            d2 = datetime.strptime(final, "%d/%m/%Y")

            if d2 < d1:
                print("A data final não pode ser menor que a inicial.\n")
                continue

            return inicial, final
        except ValueError as e:
            print(f"Erro: {e}\n")


def resolve_dates():
    """
    Decide o período a consultar, de acordo com PERIOD_MODE no .env:

    - PERIOD_MODE=hoje
        Usa a data de hoje como inicial e final. Não pede nada no
        terminal. É o modo pensado para rodar sozinho (Task Scheduler /
        cron), por exemplo a cada 15 minutos.

    - PERIOD_MODE=manual
        Usa as datas fixas definidas em DATA_INICIAL e DATA_FINAL no
        .env (formato DD/MM/AAAA). Não pede nada no terminal. Útil
        para reprocessar sempre o mesmo período sem digitar.

    - PERIOD_MODE não definido (ou qualquer outro valor)
        Comportamento original: pergunta as datas no terminal a cada
        execução. Útil para cargas pontuais/grandes que você quer
        controlar na hora.

    Para trocar de modo, basta editar o .env — não precisa mexer no
    código nem passar argumento nenhum.
    """
    mode = os.getenv("PERIOD_MODE", "").strip().lower()

    if mode == "hoje":
        hoje = datetime.now().strftime("%d/%m/%Y")
        log(f"PERIOD_MODE=hoje: consultando apenas o dia {hoje}.")
        return hoje, hoje

    if mode == "manual":
        inicial_raw = os.getenv("DATA_INICIAL", "").strip()
        final_raw = os.getenv("DATA_FINAL", "").strip()

        if not inicial_raw or not final_raw:
            raise RuntimeError(
                "PERIOD_MODE=manual requer DATA_INICIAL e DATA_FINAL "
                "definidos no .env (formato DD/MM/AAAA)."
            )

        inicial = normalize_date(inicial_raw)
        final = normalize_date(final_raw)

        d1 = datetime.strptime(inicial, "%d/%m/%Y")
        d2 = datetime.strptime(final, "%d/%m/%Y")
        if d2 < d1:
            raise RuntimeError(
                "DATA_FINAL não pode ser menor que DATA_INICIAL no .env."
            )

        log(f"PERIOD_MODE=manual: consultando de {inicial} até {final}.")
        return inicial, final

    return ask_dates()


def make_driver():
    DOWNLOAD_DIR.mkdir(exist_ok=True)

    # Remove arquivos temporários antigos.
    for p in DOWNLOAD_DIR.iterdir():
        if p.is_file() and p.name.endswith((".crdownload", ".tmp")):
            try:
                p.unlink()
            except Exception:
                pass

    options = webdriver.ChromeOptions()

    headless = os.getenv("HEADLESS", "true").strip().lower() in (
        "1", "true", "yes", "sim"
    )

    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-gpu")
    else:
        options.add_argument("--start-maximized")

    options.add_argument("--disable-save-password-bubble")
    options.add_argument("--disable-features=PasswordLeakDetection")
    options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(DOWNLOAD_DIR.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
            "credentials_enable_service": False,
            "profile.password_manager_leak_detection": False,
        },
    )

    driver = webdriver.Chrome(options=options)

    # Em modo headless (--headless=new) o Chrome bloqueia downloads por
    # padrão, mesmo com "download.default_directory" configurado nas
    # prefs. É necessário liberar explicitamente via CDP.
    if headless:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {
                "behavior": "allow",
                "downloadPath": str(DOWNLOAD_DIR.resolve()),
            },
        )
        log("Downloads liberados via CDP (modo headless).")

    # Períodos grandes podem deixar o renderer ocupado.
    driver.set_page_load_timeout(180)
    driver.set_script_timeout(180)

    return driver

def visible_inputs(driver):
    return [
        e for e in driver.find_elements(By.TAG_NAME, "input")
        if e.is_displayed() and e.get_attribute("type") not in ("hidden", "submit", "button")
    ]


def fill_element(element, value):
    element.click()
    element.send_keys(Keys.CONTROL, "a")
    element.send_keys(value)
    element.send_keys(Keys.TAB)


def find_login_field(driver, candidates, index_fallback):
    # Tenta por id/name/placeholder/autocomplete.
    for attr in ("id", "name", "placeholder", "autocomplete"):
        for candidate in candidates:
            xpath = f"//input[contains(translate(@{attr}, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{candidate.lower()}')]"
            elems = driver.find_elements(By.XPATH, xpath)
            for e in elems:
                if e.is_displayed() and e.is_enabled():
                    return e

    inputs = visible_inputs(driver)
    if len(inputs) > index_fallback:
        return inputs[index_fallback]

    raise NoSuchElementException(f"Campo não encontrado: {candidates}")


def login(driver):
    log("Abrindo PrimeBuilder...")
    driver.get(PRIME_LOGIN_URL)

    wait = WebDriverWait(driver, 30)

    # Na tela mostrada: Empresa, Usuário e Senha.
    empresa = find_login_field(driver, ["empresa", "company"], 0)
    usuario = find_login_field(driver, ["usu", "user", "login"], 1)
    senha = find_login_field(driver, ["senha", "password", "pass"], 2)

    fill_element(empresa, PRIME_EMPRESA)
    fill_element(usuario, PRIME_USUARIO)
    fill_element(senha, PRIME_SENHA)

    # Tenta o botão "Entrar" por texto; fallback para submit.
    buttons = driver.find_elements(
        By.XPATH,
        "//button[normalize-space()='Entrar'] | "
        "//input[@type='submit'] | "
        "//input[@value='Entrar'] | "
        "//a[normalize-space()='Entrar']",
    )

    clicked = False
    for button in buttons:
        if button.is_displayed() and button.is_enabled():
            button.click()
            clicked = True
            break

    if not clicked:
        senha.send_keys(Keys.ENTER)

    time.sleep(2)
    log("Login enviado.")

    # Se o sistema já estiver na tela correta, não há problema.
    if "TransitionsLayoutReport" not in driver.current_url:
        driver.get(PRIME_REPORT_URL)

    wait.until(lambda d: "TransitionsLayoutReport" in d.current_url or len(visible_inputs(d)) > 0)
    log("Relatório aberto.")


def find_report_inputs(driver):
    """
    Localiza os dois inputs de Data de Agendamento.
    Primeiro tenta pelo texto do rótulo e depois usa uma heurística:
    inputs de texto que já tenham uma data ou estejam próximos do rótulo.
    """
    # XPath relativo ao texto do rótulo.
    label_nodes = driver.find_elements(
        By.XPATH,
        "//*[contains(normalize-space(text()), 'Data de Agendamento')]",
    )

    for label in label_nodes:
        try:
            parent = label.find_element(By.XPATH, "./..")
            inputs = [
                e for e in parent.find_elements(By.XPATH, ".//input")
                if e.is_displayed() and e.is_enabled()
                and e.get_attribute("type") not in ("hidden", "button", "submit")
            ]
            if len(inputs) >= 2:
                return inputs[0], inputs[1]
        except Exception:
            pass

    # Fallback: procura inputs de texto visíveis com valor no padrão de data.
    inputs = [
        e for e in visible_inputs(driver)
        if e.get_attribute("type") in (None, "", "text")
    ]

    date_like = []
    for e in inputs:
        value = (e.get_attribute("value") or "").strip()
        placeholder = (e.get_attribute("placeholder") or "").lower()
        aria = (e.get_attribute("aria-label") or "").lower()
        if re.match(r"^\d{2}/\d{2}/\d{4}$", value) or "data" in placeholder or "data" in aria:
            date_like.append(e)

    if len(date_like) >= 2:
        return date_like[0], date_like[1]

    # Último fallback: procurar por inputs próximos ao texto no DOM.
    xpath = (
        "//*[contains(normalize-space(.), 'Data de Agendamento')]/"
        "following::input[not(@type='hidden')][position()<=2]"
    )
    elems = [e for e in driver.find_elements(By.XPATH, xpath) if e.is_displayed()]
    if len(elems) >= 2:
        return elems[0], elems[1]

    raise NoSuchElementException(
        "Não consegui localizar os dois campos de Data de Agendamento."
    )


def set_date_input(driver, element, value):
    # Tenta interação normal primeiro.
    try:
        fill_element(element, value)
        current = (element.get_attribute("value") or "").strip()
        if current == value:
            return
    except Exception:
        pass

    # Fallback para páginas antigas com datepicker/jQuery.
    driver.execute_script(
        """
        const el = arguments[0];
        const value = arguments[1];
        el.removeAttribute('readonly');
        el.value = value;
        el.dispatchEvent(new Event('input', {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
        el.dispatchEvent(new Event('blur', {bubbles:true}));
        """,
        element,
        value,
    )


def normalize_text(text):
    return " ".join((text or "").split()).strip().casefold()


def find_select_by_exact_option(driver, target_text):
    target = normalize_text(target_text)
    for select in driver.find_elements(By.TAG_NAME, "select"):
        if not select.is_displayed() or not select.is_enabled():
            continue
        options = select.find_elements(By.TAG_NAME, "option")
        texts = [normalize_text(o.text) for o in options]
        if target in texts:
            return select
    return None


def select_model(driver):
    target = "RS - Gerente"
    log(f"Selecionando modelo EXATO: '{target}'...")

    select = find_select_by_exact_option(driver, target)
    if select is None:
        # Diagnóstico útil caso a página mude.
        for i, s in enumerate(driver.find_elements(By.TAG_NAME, "select")):
            if s.is_displayed():
                texts = [o.text.strip() for o in s.find_elements(By.TAG_NAME, "option")]
                log(f"Select visível {i}: {texts[:15]}")
        raise NoSuchElementException(
            f"Opção exata '{target}' não encontrada em nenhum select visível."
        )

    options = select.find_elements(By.TAG_NAME, "option")
    target_option = next(o for o in options if normalize_text(o.text) == normalize_text(target))

    # Usa o elemento real do formulário e dispara change/input para páginas antigas.
    driver.execute_script(
        """
        const select = arguments[0];
        const value = arguments[1];
        for (const option of select.options) {
            option.selected = (option.value === value);
        }
        select.value = value;
        select.dispatchEvent(new Event('input', {bubbles:true}));
        select.dispatchEvent(new Event('change', {bubbles:true}));
        """,
        select,
        target_option.get_attribute("value"),
    )

    time.sleep(1)
    selected = driver.execute_script(
        "return arguments[0].options[arguments[0].selectedIndex].text;", select
    )
    log(f"Modelo selecionado: {selected.strip()}")

    if normalize_text(selected) != normalize_text(target):
        raise RuntimeError(
            f"Falha ao selecionar modelo. Esperado '{target}', atual '{selected}'."
        )


def find_checkbox_by_label(driver, label_text):
    """Localiza um <input type="checkbox"> a partir do texto do label associado.

    Tenta, em ordem:
    1) <label for="..."> apontando para o id do checkbox;
    2) checkbox dentro do próprio <label>;
    3) checkbox logo antes do <label> na árvore (padrão <input><label>Texto</label>).
    """
    target = normalize_text(label_text)

    for lbl in driver.find_elements(By.XPATH, "//label"):
        try:
            if not lbl.is_displayed():
                continue
            if target not in normalize_text(lbl.text):
                continue
        except Exception:
            continue

        for_attr = lbl.get_attribute("for")
        if for_attr:
            try:
                cb = driver.find_element(By.ID, for_attr)
                if cb.get_attribute("type") == "checkbox":
                    return cb
            except Exception:
                pass

        try:
            return lbl.find_element(By.XPATH, ".//input[@type='checkbox']")
        except Exception:
            pass

        try:
            return lbl.find_element(By.XPATH, "preceding-sibling::input[@type='checkbox'][1]")
        except Exception:
            pass

    # Fallback: alguns formulários não usam <label>, e sim um texto solto
    # (span/div) ao lado do checkbox. Procura o checkbox mais próximo de
    # qualquer elemento visível cujo texto bata com o alvo.
    xpath_text = (
        "//*[self::span or self::div or self::td]"
        f"[contains(translate(normalize-space(.), "
        f"'ÁÀÂÃÉÊÍÓÔÕÚÜÇ', 'AAAAEEIOOOUUC'), "
        f"translate('{label_text.upper()}', 'ÁÀÂÃÉÊÍÓÔÕÚÜÇ', 'AAAAEEIOOOUUC'))]"
    )
    for el in driver.find_elements(By.XPATH, xpath_text):
        try:
            if not el.is_displayed():
                continue
            cb = el.find_element(By.XPATH, "preceding::input[@type='checkbox'][1]")
            return cb
        except Exception:
            continue

    return None


def check_only_last_transition(driver):
    """Marca a caixa 'Somente última transição' no filtro do relatório.

    Isso evita que o CSV traga uma linha por transição/fase da OS ao invés
    de uma linha por OS, o que também ajuda a manter o upsert do Sheets
    estável (chave única por OS).
    """
    label = "Somente última transição"
    log(f"Marcando a opção '{label}'...")

    checkbox = find_checkbox_by_label(driver, label)
    if checkbox is None:
        raise NoSuchElementException(
            f"Checkbox '{label}' não encontrado no filtro do relatório."
        )

    if not checkbox.is_selected():
        try:
            checkbox.click()
        except Exception:
            driver.execute_script("arguments[0].click();", checkbox)
            driver.execute_script(
                "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                checkbox,
            )

    time.sleep(0.3)
    confirmado = checkbox.is_selected()
    log(f"'{label}' marcada: {confirmado}")

    if not confirmado:
        raise RuntimeError(f"Falha ao marcar a caixa '{label}'.")


def select_only_finalizada(driver):
    target = "Finalizada"
    log("Selecionando SOMENTE o status 'Finalizada'...")

    # Procura o select que contém as opções de status.
    status_select = None
    for select in driver.find_elements(By.TAG_NAME, "select"):
        if not select.is_displayed() or not select.is_enabled():
            continue
        texts = {normalize_text(o.text) for o in select.find_elements(By.TAG_NAME, "option")}
        if normalize_text(target) in texts and (
            normalize_text("Ativa") in texts
            or normalize_text("Sincronizada") in texts
            or normalize_text("Cancelada") in texts
        ):
            status_select = select
            break

    if status_select is None:
        raise NoSuchElementException("Select de Status não encontrado.")

    options = status_select.find_elements(By.TAG_NAME, "option")
    finalizada = next(
        (o for o in options if normalize_text(o.text) == normalize_text(target)),
        None,
    )
    if finalizada is None:
        raise NoSuchElementException("Opção 'Finalizada' não encontrada no Status.")

    # Desmarca TODOS e marca somente Finalizada.
    driver.execute_script(
        """
        const select = arguments[0];
        const targetValue = arguments[1];
        for (const option of select.options) {
            option.selected = (option.value === targetValue);
        }
        select.value = targetValue;
        select.dispatchEvent(new Event('input', {bubbles:true}));
        select.dispatchEvent(new Event('change', {bubbles:true}));
        """,
        status_select,
        finalizada.get_attribute("value"),
    )

    time.sleep(0.5)
    selected = driver.execute_script(
        "return Array.from(arguments[0].selectedOptions).map(o => o.text.trim());",
        status_select,
    )
    log(f"Status selecionado: {selected}")

    if [normalize_text(x) for x in selected] != [normalize_text(target)]:
        raise RuntimeError(f"Falha ao selecionar somente Finalizada. Atual: {selected}")

def clear_old_downloads():
    """Remove CSVs antigos para não confundir o resultado da exportação atual."""
    for p in DOWNLOAD_DIR.glob("*.csv"):
        try:
            p.unlink()
            log(f"CSV antigo removido: {p.name}")
        except Exception as exc:
            log(f"Não foi possível remover {p.name}: {exc}")


def click_export(driver):
    """
    Aciona a exportação. Em relatórios grandes o PrimeBuilder pode
    bloquear o renderer por mais de 60 segundos. Nesse caso o timeout
    do Selenium é tratado como possível exportação em andamento e o
    programa passa a monitorar a pasta de downloads.
    """
    log("Procurando 'Exportar - Fases por Coluna'...")

    xpath = (
        "//*[self::button or self::input or self::a]"
        "[contains(normalize-space(.), 'Exportar - Fases por Coluna')"
        " or contains(@value, 'Exportar - Fases por Coluna')"
        " or contains(@title, 'Exportar - Fases por Coluna')]"
    )

    elems = driver.find_elements(By.XPATH, xpath)

    if not elems:
        elems = driver.find_elements(
            By.XPATH,
            "//*[contains(normalize-space(.), 'Exportar - Fases por Coluna')]"
        )

    elemento = None
    for e in elems:
        try:
            if e.is_displayed() and e.is_enabled():
                elemento = e
                break
        except Exception:
            continue

    if elemento is None:
        raise NoSuchElementException(
            "Botão 'Exportar - Fases por Coluna' não encontrado ou indisponível."
        )

    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', inline:'center'});",
            elemento,
        )
    except Exception:
        pass

    log("Botão encontrado. Enviando clique...")

    try:
        elemento.click()
        log("Clique enviado. Aguardando geração do relatório...")
    except TimeoutException:
        log(
            "O Chrome ficou ocupado por mais de 60 segundos. "
            "Isso pode ser normal em relatórios grandes."
        )
        log("Não haverá novo clique. Monitorando a pasta de downloads...")
    except Exception as e:
        # Para erros que não sejam o timeout do renderer, tenta JS.
        try:
            driver.execute_script("arguments[0].click();", elemento)
            log("Clique enviado via JavaScript.")
        except TimeoutException:
            log(
                "Chrome continua ocupado. Não será feito novo clique; "
                "o robô aguardará o CSV."
            )
        except Exception:
            raise RuntimeError(
                f"Não foi possível acionar a exportação: {e}"
            )



def wait_for_download(timeout=None):
    if timeout is None:
        timeout = int(os.getenv("DOWNLOAD_TIMEOUT_SECONDS", "1800"))
    log(f"Aguardando CSV por até {timeout // 60} minutos...")
    end = time.time() + timeout

    previous = set(DOWNLOAD_DIR.glob("*"))

    while time.time() < end:
        time.sleep(1)

        # Ignora arquivos temporários do Chrome.
        files = [
            p for p in DOWNLOAD_DIR.iterdir()
            if p.is_file() and not p.name.endswith((".crdownload", ".tmp"))
        ]

        new_files = [p for p in files if p not in previous]
        csvs = [p for p in new_files if p.suffix.lower() == ".csv"]

        if csvs:
            # Garante que o arquivo terminou de ser escrito.
            target = max(csvs, key=lambda p: p.stat().st_mtime)
            size1 = target.stat().st_size
            time.sleep(1)
            size2 = target.stat().st_size

            if size1 == size2 and size2 > 0:
                log(f"Download concluído: {target.name}")
                return target

    raise TimeoutException("O CSV não foi baixado dentro do tempo esperado.")


def read_csv(csv_path):
    log("Lendo CSV...")

    # O arquivo enviado pelo usuário veio separado por ; e em ANSI/Latin-1.
    last_error = None
    for encoding in ("cp1252", "latin1", "utf-8-sig", "utf-8"):
        try:
            df = pd.read_csv(
                csv_path,
                sep=";",
                encoding=encoding,
                dtype=str,
                keep_default_na=False,
            )
            break
        except Exception as e:
            last_error = e
    else:
        raise last_error

    # Remove coluna vazia criada pelo relatório.
    empty_cols = [
        c for c in df.columns
        if not c.strip() or c.startswith("Unnamed:")
    ]
    if empty_cols:
        df = df.drop(columns=empty_cols)

    df = df.fillna("")
    df.columns = [str(c).strip() for c in df.columns]

    log(f"Registros no CSV: {len(df)}")
    log(f"Colunas: {len(df.columns)}")
    return df


def get_worksheet():
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(
            f"Service Account não encontrada em: {CREDENTIALS_FILE}"
        )

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_file(
        CREDENTIALS_FILE,
        scopes=scopes,
    )
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(SHEET_ID)

    if WORKSHEET_NAME:
        return spreadsheet.worksheet(WORKSHEET_NAME)

    return spreadsheet.sheet1


def call_with_retry(func, *args, max_attempts=5, **kwargs):
    """Executa uma chamada à API do Sheets com backoff em caso de erro 429."""
    import gspread

    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            is_quota = "429" in str(e) or "Quota exceeded" in str(e)
            if not is_quota or attempt == max_attempts:
                raise
            wait = 2 ** attempt  # 2, 4, 8, 16, 32s
            log(f"Cota do Sheets atingida (tentativa {attempt}/{max_attempts}). "
                f"Aguardando {wait}s...")
            time.sleep(wait)

def upsert_sheet(df):
    """
    Atualiza a planilha por chave sem apagar dados existentes.

    Regras:
    - NUNCA usa ws.clear().
    - Mantém as colunas já existentes na planilha.
    - Se o CSV trouxer colunas novas, adiciona essas colunas ao final.
    - Se a ordem das colunas do CSV mudar, faz o mapeamento pelo nome.
    - Registros existentes são atualizados pela UPSERT_KEY.
    - Registros novos são adicionados no final.
    """
    ws = get_worksheet()

    if UPSERT_KEY not in df.columns:
        raise ValueError(
            f"A coluna-chave '{UPSERT_KEY}' não existe no CSV. "
            f"Colunas disponíveis: {list(df.columns)}"
        )

    # Remove linhas sem chave.
    df = df[df[UPSERT_KEY].astype(str).str.strip() != ""].copy()

    # Cabeçalho vindo do CSV.
    csv_headers = [str(c).strip() for c in df.columns]

    # Leitura atual da planilha.
    existing = ws.get_all_values()

    # ============================================================
    # CABEÇALHO
    # ============================================================
    if not existing:
        sheet_headers = csv_headers[:]
        ws.update(
            range_name="A1",
            values=[sheet_headers],
            value_input_option="RAW"
        )
        existing = [sheet_headers]
        log("Planilha vazia: cabeçalho criado.")
    else:
        sheet_headers = [str(c).strip() for c in existing[0]]

        # Remove células vazias do final do cabeçalho.
        while sheet_headers and sheet_headers[-1] == "":
            sheet_headers.pop()

        # Adiciona somente colunas novas. NUNCA limpa a planilha.
        new_headers = [c for c in csv_headers if c not in sheet_headers]

        if new_headers:
            old_count = len(sheet_headers)
            sheet_headers.extend(new_headers)

            # Atualiza somente a linha do cabeçalho, preservando os dados.
            from gspread.utils import rowcol_to_a1
            start_col = rowcol_to_a1(1, old_count + 1).replace("1", "")
            end_col = rowcol_to_a1(1, len(sheet_headers)).replace("1", "")

            # Garante que a grade da aba tem colunas suficientes.
            # O Sheets não cria colunas sozinho num update de range.
            if len(sheet_headers) > ws.col_count:
                faltam = len(sheet_headers) - ws.col_count
                log(
                    f"Expandindo a aba '{ws.title}': +{faltam} coluna(s) "
                    f"({ws.col_count} -> {len(sheet_headers)})"
                )
                ws.add_cols(faltam)

            ws.update(
                range_name=f"{start_col}1:{end_col}1",
                values=[new_headers],
                value_input_option="RAW"
            )

            log(
                f"Novas colunas adicionadas ao final da planilha: "
                f"{new_headers}"
            )

        # Se a planilha estava com cabeçalho menor/incompleto, garante
        # que o cabeçalho completo esteja gravado sem apagar nenhuma linha.
        if len(existing[0]) != len(sheet_headers) or existing[0] != sheet_headers:
            ws.update(
                range_name="A1",
                values=[sheet_headers],
                value_input_option="RAW"
            )

            # Recarrega para trabalhar com o cabeçalho atualizado.
            existing = ws.get_all_values()

    # ============================================================
    # MAPEAMENTO DAS COLUNAS
    # ============================================================
    key_col = sheet_headers.index(UPSERT_KEY)

    # Índice das colunas do CSV.
    csv_index = {header: i for i, header in enumerate(csv_headers)}

    # Converte uma linha do CSV para a ordem das colunas da planilha.
    def normalize_csv_row(csv_row):
        return [
            str(csv_row[csv_index[h]]) if h in csv_index else ""
            for h in sheet_headers
        ]

    # ============================================================
    # MAPA DAS LINHAS EXISTENTES
    # ============================================================
    row_by_key = {}

    for row_number, row in enumerate(existing[1:], start=2):
        if len(row) > key_col:
            key = str(row[key_col]).strip()

            if key:
                padded = row + [""] * (len(sheet_headers) - len(row))
                row_by_key[key] = (
                    row_number,
                    padded[:len(sheet_headers)]
                )

    updates = []
    appends = []
    unchanged = 0

    # ============================================================
    # UPSERT
    # ============================================================
    for csv_row in df.astype(str).values.tolist():
        row = normalize_csv_row(csv_row)
        key = row[key_col].strip()

        if not key:
            continue

        if key in row_by_key:
            row_number, current_row = row_by_key[key]

            if current_row == row:
                unchanged += 1
            else:
                updates.append((row_number, row))
        else:
            appends.append(row)

    # ============================================================
    # ATUALIZAÇÕES
    # ============================================================
    if updates:
        from gspread.utils import rowcol_to_a1

        BATCH_SIZE = 500
        SLEEP_BETWEEN_BATCHES = 1.2

        for i in range(0, len(updates), BATCH_SIZE):
            chunk = updates[i:i + BATCH_SIZE]
            body = []

            for row_number, row in chunk:
                start = rowcol_to_a1(row_number, 1)
                end = rowcol_to_a1(row_number, len(sheet_headers))

                body.append({
                    "range": f"{start}:{end}",
                    "values": [row]
                })

            call_with_retry(
                ws.batch_update,
                body,
                value_input_option="RAW"
            )

            log(
                f"Lote de atualização enviado: "
                f"{len(chunk)} linhas "
                f"({i + len(chunk)}/{len(updates)})"
            )

            if i + BATCH_SIZE < len(updates):
                time.sleep(SLEEP_BETWEEN_BATCHES)

    # ============================================================
    # NOVOS REGISTROS
    # ============================================================
    if appends:
        call_with_retry(
            ws.append_rows,
            appends,
            value_input_option="RAW"
        )

    log(f"Atualizados: {len(updates)}")
    log(f"Inseridos:   {len(appends)}")
    log(f"Sem mudança: {unchanged}")
    log(f"Total CSV:   {len(df)}")
    log(f"Colunas CSV: {len(csv_headers)}")
    log(f"Colunas Sheet: {len(sheet_headers)}")

def main():
    inicio_execucao = time.perf_counter()

    log("=" * 70)
    log("ROBÔ PRIMEBUILDER → GOOGLE SHEETS | INÍCIO DA EXECUÇÃO")
    log(f"Diretório do projeto: {BASE_DIR}")
    log(f"Python: {os.sys.executable}")
    log(f"PERIOD_MODE: {os.getenv('PERIOD_MODE', '').strip() or 'manual/terminal'}")
    log("=" * 70)

    if not PRIME_SENHA:
        raise RuntimeError(
            "PRIME_SENHA não foi configurada no arquivo .env."
        )

    inicial, final = resolve_dates()

    driver = None
    try:
        driver = make_driver()
        login(driver)
        time.sleep(2)
        pbnl.fechar_popups(driver, log)

        # Filtros e exportação: pedido direto ao site (layout de 24/11/2026).
        pbnl.fechar_popups(driver, log)
        antes_exportacao = pbnl.snapshot_exportacoes(driver, DOWNLOAD_DIR, log)
        pbnl.exportar_via_api(driver, log, MODELO_RELATORIO, inicial, final)

        # Relatórios de períodos longos podem demorar vários minutos.
        csv_path = pbnl.wait_for_export(driver, log, DOWNLOAD_DIR, MODELO_RELATORIO, antes_exportacao, timeout=600)
        if csv_path is None:
            log("Nenhum registro no período. Planilha não foi alterada.", "SUCCESS")
            return

        df = read_csv(csv_path)
        upsert_sheet(df)

        duracao = time.perf_counter() - inicio_execucao
        log(f"PROCESSO CONCLUÍDO COM SUCESSO! Duração: {duracao:.1f}s", level="SUCCESS")

    except Exception as e:
        log_exception("ERRO FATAL", e)
        if driver:
            try:
                screenshot = BASE_DIR / "erro_primebuilder.png"
                driver.save_screenshot(str(screenshot))
                log(f"Screenshot salvo em: {screenshot}")
            except Exception as screenshot_error:
                log_exception("Não foi possível salvar o screenshot", screenshot_error)
        raise
    finally:
        if driver:
            try:
                driver.quit()
                log("Navegador encerrado.")
            except Exception as quit_error:
                log_exception("Erro ao encerrar o navegador", quit_error)

        duracao = time.perf_counter() - inicio_execucao
        log(f"FIM DA EXECUÇÃO | Duração total: {duracao:.1f}s")


if __name__ == "__main__":
    main()
