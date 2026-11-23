"""
Adaptações para o novo layout do PrimeBuilder (Visões de Dados, nov/2026).

O que mudou no site:

1. O filtro "Status" deixou de ser um <select> visível. Agora é um
   dropdown customizado ("Escolher"). O <select> original pode continuar
   existindo escondido, ou pode ter sido substituído por completo.

2. "Exportar - Fases por Coluna" não baixa mais o arquivo na hora.
   Ele cria um pedido na tabela "Exportações". Quando a Situação fica
   "Pronta", é preciso clicar em "Baixar".

Este módulo tenta as duas formas (antiga e nova) para não quebrar se o
site voltar atrás ou mudar de novo. Quando não consegue, grava um
arquivo de diagnóstico com o HTML da região, para facilitar o ajuste.
"""

import os
import time
import unicodedata
from pathlib import Path

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys


STATUS_CONHECIDOS = ("ativa", "sincronizada", "cancelada", "finalizada", "pendente")


def _norm(text):
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


def _salvar_diagnostico(driver, nome, base_dir):
    """Grava HTML da página para descobrirmos a estrutura nova."""
    try:
        destino = Path(base_dir) / f"diagnostico_{nome}.html"
        destino.write_text(driver.page_source, encoding="utf-8")
        return destino
    except Exception:
        return None


# ============================================================
# STATUS
# ============================================================

_JS_SELECT_STATUS_NATIVO = r"""
const alvo = arguments[0];
const norm = s => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '')
                    .toLowerCase().replace(/\s+/g, ' ').trim();
const conhecidos = arguments[1];

// Procura QUALQUER <select>, inclusive escondido (plugins escondem o original).
for (const sel of document.querySelectorAll('select')) {
    const textos = [...sel.options].map(o => norm(o.text));
    if (!textos.includes(norm(alvo))) continue;
    const outros = textos.filter(t => conhecidos.includes(t) && t !== norm(alvo));
    if (outros.length === 0) continue;

    for (const o of sel.options) o.selected = norm(o.text) === norm(alvo);

    sel.dispatchEvent(new Event('input', {bubbles: true}));
    sel.dispatchEvent(new Event('change', {bubbles: true}));

    // Atualiza plugins jQuery comuns, se existirem.
    try {
        if (window.jQuery) {
            const $s = window.jQuery(sel);
            if ($s.selectpicker) $s.selectpicker('refresh');
            if ($s.multiselect) $s.multiselect('refresh');
            $s.trigger('change');
        }
    } catch (e) {}
    // Tom Select / Choices.js guardam a instância no elemento.
    try {
        if (sel.tomselect) { sel.tomselect.setValue(
            [...sel.options].filter(o => o.selected).map(o => o.value)); }
    } catch (e) {}

    return [...sel.selectedOptions].map(o => o.text.trim());
}
return null;
"""


def _achar_gatilho_status(driver):
    """Acha o elemento clicável do dropdown que fica abaixo do rótulo 'Status'."""
    rotulos = driver.find_elements(
        By.XPATH,
        "//*[self::label or self::span or self::div or self::p]"
        "[normalize-space(text())='Status']",
    )
    for rotulo in rotulos:
        if not rotulo.is_displayed():
            continue
        candidatos = rotulo.find_elements(
            By.XPATH,
            "following::*[self::button or self::input or @role='combobox' "
            "or contains(normalize-space(.), 'Escolher')][1]",
        )
        for c in candidatos:
            if c.is_displayed():
                return c
    return None


def _clicar_opcao(driver, texto):
    """Clica na opção visível cujo texto é exatamente `texto`."""
    alvo = _norm(texto)
    elems = driver.find_elements(
        By.XPATH,
        "//*[@role='option' or self::li or self::label or self::span or self::div]"
        f"[contains(normalize-space(.), '{texto}')]",
    )
    # Prefere o elemento mais interno (menor texto) e visível.
    elems = [e for e in elems if e.is_displayed() and _norm(e.text) == alvo]
    if not elems:
        return False
    el = elems[-1]
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click();", el)
    return True


def select_only_finalizada(driver, log, base_dir):
    target = "Finalizada"
    log("Selecionando SOMENTE o status 'Finalizada'...")

    # Espera um pouco: o filtro pode ser carregado depois do modelo.
    fim = time.time() + 15
    selecionados = None
    while time.time() < fim:
        selecionados = driver.execute_script(
            _JS_SELECT_STATUS_NATIVO, target, list(STATUS_CONHECIDOS)
        )
        if selecionados is not None:
            break
        if _achar_gatilho_status(driver) is not None:
            break
        time.sleep(1)

    # --- Caminho 1: existe <select> (visível ou escondido) ---
    if selecionados is not None:
        log(f"Status selecionado (select): {selecionados}")
        if [_norm(x) for x in selecionados] != [_norm(target)]:
            raise RuntimeError(
                f"Falha ao selecionar somente Finalizada. Atual: {selecionados}"
            )
        _conferir_rotulo_status(driver, log, target)
        return

    # --- Caminho 2: dropdown customizado, clicando como um usuário ---
    gatilho = _achar_gatilho_status(driver)
    if gatilho is None:
        arq = _salvar_diagnostico(driver, "status", base_dir)
        raise NoSuchElementException(
            "Filtro de Status não encontrado (nem select, nem dropdown). "
            f"HTML salvo em: {arq}"
        )

    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", gatilho)
    gatilho.click()
    time.sleep(0.8)

    # Desmarca o que já estiver marcado (exceto Finalizada).
    marcados = driver.find_elements(
        By.XPATH,
        "//*[@role='option' and @aria-selected='true'] | "
        "//input[@type='checkbox' and @checked]/ancestor::*[self::li or self::label][1]",
    )
    for m in marcados:
        if m.is_displayed() and _norm(m.text) in STATUS_CONHECIDOS and _norm(m.text) != _norm(target):
            try:
                m.click()
            except Exception:
                driver.execute_script("arguments[0].click();", m)
            time.sleep(0.3)

    if not _clicar_opcao(driver, target):
        arq = _salvar_diagnostico(driver, "status_aberto", base_dir)
        raise NoSuchElementException(
            f"Opção 'Finalizada' não encontrada no dropdown de Status. HTML salvo em: {arq}"
        )

    time.sleep(0.5)
    # Fecha o dropdown.
    try:
        driver.switch_to.active_element.send_keys(Keys.ESCAPE)
    except Exception:
        pass
    driver.execute_script("document.body.click();")
    time.sleep(0.5)

    _conferir_rotulo_status(driver, log, target, obrigatorio=True)


def _conferir_rotulo_status(driver, log, target, obrigatorio=False):
    gatilho = _achar_gatilho_status(driver)
    texto = _norm(gatilho.text if gatilho else "")
    log(f"Texto exibido no filtro Status: '{gatilho.text.strip() if gatilho else ''}'")
    if obrigatorio and texto != _norm(target):
        raise RuntimeError(
            f"Status não ficou somente 'Finalizada'. Exibido: '{texto}'"
        )


# ============================================================
# EXPORTAÇÃO ASSÍNCRONA (tabela "Exportações")
# ============================================================

_JS_LINHAS_EXPORTACAO = r"""
const norm = s => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '')
                    .toLowerCase().replace(/\s+/g, ' ').trim();
for (const t of document.querySelectorAll('table')) {
    const cab = [...t.querySelectorAll('th')].map(th => norm(th.innerText));
    if (!cab.includes('modelo') || !cab.some(c => c.startsWith('situa'))) continue;
    const iPed = cab.findIndex(c => c.startsWith('pedida'));
    const iMod = cab.indexOf('modelo');
    const iTipo = cab.indexOf('tipo');
    const iSit = cab.findIndex(c => c.startsWith('situa'));
    const iLin = cab.indexOf('linhas');
    return [...t.querySelectorAll('tbody tr')].map((tr, idx) => {
        const td = [...tr.querySelectorAll('td')].map(x => x.innerText.trim());
        return {
            idx,
            pedida: td[iPed] || '',
            modelo: td[iMod] || '',
            tipo: td[iTipo] || '',
            situacao: td[iSit] || '',
            linhas: iLin >= 0 ? td[iLin] : '',
        };
    });
}
return null;
"""

_JS_CLICAR_BAIXAR = r"""
const idx = arguments[0];
const norm = s => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '')
                    .toLowerCase().replace(/\s+/g, ' ').trim();
for (const t of document.querySelectorAll('table')) {
    const cab = [...t.querySelectorAll('th')].map(th => norm(th.innerText));
    if (!cab.includes('modelo')) continue;
    const tr = t.querySelectorAll('tbody tr')[idx];
    if (!tr) return false;
    const link = [...tr.querySelectorAll('a, button')]
        .find(a => norm(a.innerText) === 'baixar');
    if (!link) return false;
    link.click();
    return true;
}
return false;
"""


def snapshot_exportacoes(driver, download_dir=None, log=print):
    """Lista as exportações já existentes ANTES do clique.

    Se receber a pasta de downloads, também limpa arquivos antigos.
    """
    if download_dir is not None:
        limpar_downloads_antigos(download_dir, log)
    linhas = driver.execute_script(_JS_LINHAS_EXPORTACAO) or []
    return {(l["pedida"], l["modelo"], l["tipo"]) for l in linhas}


def limpar_downloads_antigos(download_dir, log=print):
    """Apaga .csv/.zip/.xlsx antigos da pasta de downloads.

    Se o arquivo antigo continuar lá, o Chrome pode sobrescrever o mesmo
    nome e o robô não percebe que chegou um arquivo novo.
    """
    for p in Path(download_dir).glob("*"):
        if p.is_file() and p.suffix.lower() in (".csv", ".zip", ".xlsx"):
            try:
                p.unlink()
                log(f"Arquivo antigo removido: {p.name}")
            except Exception as exc:
                log(f"Não foi possível remover {p.name}: {exc}")


def _arquivos_novos(download_dir, desde):
    """Arquivos modificados depois de `desde` (detecta também sobrescrita)."""
    out = []
    for p in Path(download_dir).iterdir():
        if not p.is_file() or p.stat().st_mtime < desde:
            continue
        if p.name.endswith((".crdownload", ".tmp")):
            continue
        if p.suffix.lower() in (".csv", ".zip", ".xlsx"):
            out.append(p)
    return out


_JS_INFO_BAIXAR = r"""
const idx = arguments[0];
const norm = s => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '')
                    .toLowerCase().replace(/\s+/g, ' ').trim();
for (const t of document.querySelectorAll('table')) {
    const cab = [...t.querySelectorAll('th')].map(th => norm(th.innerText));
    if (!cab.includes('modelo')) continue;
    const tr = t.querySelectorAll('tbody tr')[idx];
    if (!tr) return null;
    const link = [...tr.querySelectorAll('a, button')]
        .find(a => norm(a.innerText) === 'baixar');
    if (!link) return null;
    return {
        href: link.href || '',
        attr: link.getAttribute('href') || '',
        target: link.getAttribute('target') || '',
        outer: link.outerHTML.slice(0, 500),
    };
}
return null;
"""

_JS_FETCH_ARQUIVO = r"""
const url = arguments[0];
const done = arguments[arguments.length - 1];
fetch(url, {credentials: 'include'})
  .then(async r => {
      const blob = await r.blob();
      const fr = new FileReader();
      fr.onload = () => done({
          ok: r.ok, status: r.status,
          cd: r.headers.get('content-disposition') || '',
          ct: r.headers.get('content-type') || '',
          data: String(fr.result).split(',')[1] || '',
      });
      fr.readAsDataURL(blob);
  })
  .catch(e => done({erro: String(e)}));
"""


def _liberar_downloads(driver, download_dir):
    """Libera downloads para o navegador inteiro (inclusive abas novas)."""
    try:
        driver.execute_cdp_cmd(
            "Browser.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(Path(download_dir).resolve())},
        )
    except Exception:
        pass


def _baixar_via_fetch(driver, log, download_dir, info):
    """
    Baixa o arquivo pelo próprio navegador (fetch com os cookies da sessão)
    e grava direto na pasta. Não depende do download do Chrome.
    """
    import base64
    import re as _re

    attr = (info.get("attr") or "").strip().lower()
    url = info.get("href") or ""
    if not url or attr in ("", "#") or attr.startswith("javascript"):
        return None

    try:
        driver.set_script_timeout(300)
    except Exception:
        pass
    try:
        r = driver.execute_async_script(_JS_FETCH_ARQUIVO, url)
    except Exception as exc:
        log(f"Download direto falhou ({exc}). Vou tentar pelo clique.")
        return None

    if not r or r.get("erro") or not r.get("ok") or not r.get("data"):
        log(f"Download direto falhou: {r and (r.get('erro') or r.get('status'))}")
        return None

    if "text/html" in (r.get("ct") or "").lower():
        log("Download direto devolveu uma página HTML (não é o arquivo). Vou tentar pelo clique.")
        return None

    nome = "OSTransitionsReportPorColunas.zip"
    m = _re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', r.get("cd") or "", _re.I)
    if m:
        nome = Path(m.group(1)).name
    elif "csv" in (r.get("ct") or "").lower():
        nome = "OSTransitionsReportPorColunas.csv"

    destino = Path(download_dir) / nome
    destino.write_bytes(base64.b64decode(r["data"]))
    log(f"Download concluído (direto): {destino.name} ({destino.stat().st_size} bytes)")
    return destino


def _voltar_para_aba_principal(driver, aba_principal):
    """Se o 'Baixar' abriu outra aba, fecha e volta para a principal."""
    try:
        for h in driver.window_handles:
            if h != aba_principal:
                driver.switch_to.window(h)
                driver.close()
        driver.switch_to.window(aba_principal)
    except Exception:
        pass


def wait_for_export(driver, log, download_dir, model_name, antes, timeout=None):
    """
    Espera o arquivo de duas formas ao mesmo tempo:
      - download direto (comportamento antigo);
      - nova linha na tabela 'Exportações' ficar 'Pronta' -> clica 'Baixar'.

    Retorna None quando a exportação fica pronta com 0 linhas.
    """
    if timeout is None:
        timeout = int(os.getenv("DOWNLOAD_TIMEOUT_SECONDS", "1800"))

    log(f"Aguardando exportação por até {timeout // 60} minutos...")

    download_dir = Path(download_dir)
    _liberar_downloads(driver, download_dir)
    aba_principal = driver.current_window_handle
    info_logada = False
    # Considera novo tudo que foi gravado a partir de pouco antes do clique
    # (por data de modificação, não por nome).
    desde = time.time() - 60
    fim = time.time() + timeout
    clicou_baixar = False
    clicou_em = 0.0
    tentativas_baixar = 0
    ultimo_status = None
    ultimo_refresh = time.time()

    while time.time() < fim:
        time.sleep(2)

        # 1) Chegou arquivo?
        novos = _arquivos_novos(download_dir, desde)
        if novos:
            alvo = max(novos, key=lambda p: p.stat().st_mtime)
            s1 = alvo.stat().st_size
            time.sleep(1.5)
            s2 = alvo.stat().st_size
            if s1 == s2 and s2 > 0:
                log(f"Download concluído: {alvo.name}")
                return alvo
            continue

        if clicou_baixar:
            # Se o download não começou, tenta clicar de novo.
            baixando = any(Path(download_dir).glob("*.crdownload"))
            if not baixando and time.time() - clicou_em > 20:
                if tentativas_baixar >= 5:
                    raise TimeoutException(
                        "Cliquei em 'Baixar' 5 vezes e nenhum arquivo chegou."
                    )
                log("Arquivo não chegou. Clicando em 'Baixar' de novo...")
                clicou_baixar = False
            else:
                continue

        # 2) Olha a tabela de exportações.
        try:
            linhas = driver.execute_script(_JS_LINHAS_EXPORTACAO) or []
        except Exception:
            linhas = []

        minha = next(
            (
                l for l in linhas
                if (l["pedida"], l["modelo"], l["tipo"]) not in antes
                and _norm(l["modelo"]) == _norm(model_name)
                and "coluna" in _norm(l["tipo"])
            ),
            None,
        )

        if minha is None:
            # A tabela pode não se atualizar sozinha. Recarrega de vez em quando
            # (os filtros não importam mais: o pedido já foi feito).
            if time.time() - ultimo_refresh > 30:
                log("Pedido ainda não apareceu na tabela. Recarregando a página...")
                driver.refresh()
                ultimo_refresh = time.time()
            continue

        if minha["situacao"] != ultimo_status:
            log(
                f"Exportação {minha['pedida']} | {minha['modelo']} | "
                f"situação: {minha['situacao']} | linhas: {minha['linhas']}"
            )
            ultimo_status = minha["situacao"]

        sit = _norm(minha["situacao"])
        if sit.startswith("pronta") and str(minha["linhas"]).strip() == "0":
            log("Exportação pronta com 0 linhas: nada para baixar neste período.")
            return None
        if sit.startswith("pronta"):
            info = driver.execute_script(_JS_INFO_BAIXAR, minha["idx"]) or {}
            if not info_logada:
                log(f"Link 'Baixar': {info.get('outer', '(não encontrado)')}")
                info_logada = True

            # 1º caminho: baixar direto pelo link (não depende do Chrome).
            arquivo = _baixar_via_fetch(driver, log, download_dir, info)
            if arquivo is not None:
                return arquivo

            # 2º caminho: clicar no botão, como um usuário.
            if driver.execute_script(_JS_CLICAR_BAIXAR, minha["idx"]):
                time.sleep(1)
                _voltar_para_aba_principal(driver, aba_principal)
                tentativas_baixar += 1
                log(f"Clique em 'Baixar' enviado (tentativa {tentativas_baixar}).")
                clicou_baixar = True
                clicou_em = time.time()
            else:
                log("Linha pronta, mas o link 'Baixar' não foi encontrado.")
        elif sit.startswith(("erro", "falh", "cancel")):
            raise RuntimeError(f"Exportação terminou com situação: {minha['situacao']}")
        elif time.time() - ultimo_refresh > 30:
            driver.refresh()
            ultimo_refresh = time.time()

    raise TimeoutException("A exportação não ficou pronta/baixada dentro do tempo esperado.")


# ============================================================
# POP-UPS ("O PrimeBuilder está de cara nova", tour, avisos)
# ============================================================

_JS_FECHAR_POPUPS = r"""
const norm = s => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '')
                    .toLowerCase().replace(/\s+/g, ' ').trim();
// Seguro clicar em qualquer lugar da página:
const textosFechar = ['agora nao', 'pular', 'pular tour', 'nao, obrigado', 'dispensar'];
// Só clica se estiver dentro de um modal/tour (evita fechar painéis da página):
const textosSoNoModal = ['fechar', 'entendi', 'ok, entendi', 'ok'];
const visivel = el => { const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0; };
const fechados = [];
for (const el of document.querySelectorAll('button, a, [role=button]')) {
    if (!visivel(el)) continue;
    const t = norm(el.innerText || el.getAttribute('aria-label') || el.title);
    const ehX = ['x', '×', 'close', 'fechar'].includes(t) ||
                norm(el.getAttribute('aria-label')).includes('fechar') ||
                norm(el.getAttribute('aria-label')).includes('close');
    const dentroDeModal = el.closest('[role=dialog], [aria-modal=true], .modal, ' +
        '[class*=modal], [class*=dialog], [class*=popover], [class*=tour], ' +
        '[class*=shepherd], [class*=introjs], [class*=driver-popover]');
    if (textosFechar.includes(t) || (dentroDeModal && (ehX || textosSoNoModal.includes(t)))) {
        try { el.click(); fechados.push(t || 'x'); } catch (e) {}
    }
}
return fechados;
"""


def fechar_popups(driver, log=print, tentativas=3):
    """Fecha pop-ups de novidade/tour que ficam na frente dos filtros."""
    from selenium.webdriver.common.keys import Keys as _K
    total = []
    for _ in range(tentativas):
        try:
            fechados = driver.execute_script(_JS_FECHAR_POPUPS) or []
        except Exception:
            fechados = []
        if not fechados:
            break
        total += fechados
        time.sleep(0.8)
    try:
        driver.find_element(By.TAG_NAME, "body").send_keys(_K.ESCAPE)
    except Exception:
        pass
    if total:
        log(f"Pop-up fechado: {total}")
