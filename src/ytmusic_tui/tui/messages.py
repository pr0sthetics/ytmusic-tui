"""Strings e formatação de mensagens da TUI (T12-R7: ponto único, pt-BR).

As mensagens visíveis ao usuário — ajuda, erros, hints e toasts — nascem
aqui; telas e controllers importam daqui. Sem framework de i18n: o objetivo
é ter **um** lugar para ajustar texto, não tradução em tempo real.
"""

from collections.abc import Iterable

from textual.binding import Binding, BindingType

from ytmusic_tui.diagnostics import mask_diagnostics

# ===== Ajuda (T12-R1) =====

HELP_TITLE = "❓ Ajuda — YouTube Music TUI"
HELP_NAV_TITLE = "Navegação"
HELP_NAV_LINES = (
    "↑/↓    navegar pelas listas",
    "Tab    alternar o foco entre os controles",
    "Enter  tocar a faixa / abrir o álbum selecionado",
    "Esc    voltar (da aba Álbum para a Busca)",
)
HELP_KEYS_TITLE = "Atalhos"
HELP_FOOTER = "Esc ou F1 fecha esta ajuda."

#: Nota por ação exibida ao lado do atalho na ajuda — espelha o
#: ``check_action`` de ``MainScreen`` (r/d só valem na aba 📥 Downloads).
HELP_NOTES: dict[str, str] = {
    "retry_download": "aba 📥 Downloads",
    "discard_download": "aba 📥 Downloads",
}

#: Nomes amigáveis das teclas na ajuda (chaves de ``Binding.key``).
_KEY_NAMES = {
    "space": "Espaço",
    "left": "←",
    "right": "→",
    "up": "↑",
    "down": "↓",
    "enter": "Enter",
    "escape": "Esc",
    "tab": "Tab",
    "backspace": "Backspace",
    "delete": "Delete",
}


def format_key(key: str) -> str:
    """Formata a tecla de um ``Binding`` para exibição.

    ``ctrl+q`` → ``Ctrl+Q``; letra maiúscula isolada é, no Textual, a
    combinação com Shift (``P`` → ``Shift+P``) — sem isso a ajuda mostraria
    ``P`` duplicado para ``p``/``P`` (correção do F1 pós-BUG-01).
    """
    parts = []
    for part in key.split("+"):
        if part in _KEY_NAMES:
            token = _KEY_NAMES[part]
        elif len(part) == 1 and part.isalpha() and part.isupper():
            token = f"Shift+{part}"
        elif len(part) == 1 or (part.startswith("f") and part[1:].isdigit()):
            token = part.upper()
        else:
            token = part.capitalize()
        parts.append(token)
    return "+".join(parts)


def _as_binding(entry: BindingType) -> Binding:
    """Normaliza uma entrada de ``BINDINGS`` (o Textual aceita tuplas também)."""
    if isinstance(entry, Binding):
        return entry
    return Binding(*entry)


def help_shortcut_lines(bindings: Iterable[BindingType]) -> list[str]:
    """Linhas de atalhos da ajuda, sem duplicar tecla (a 1ª ocorrência vale).

    Recebe ``App.BINDINGS + MainScreen.BINDINGS`` na ordem de exibição; a
    descrição vem do próprio binding (fonte única), com a nota por ação
    quando houver (ex.: r/d restritos à aba 📥 Downloads).
    """
    seen: set[str] = set()
    lines: list[str] = []
    for entry in bindings:
        binding = _as_binding(entry)
        if binding.key in seen:
            continue
        seen.add(binding.key)
        note = HELP_NOTES.get(binding.action)
        suffix = f"  [dim]({note})[/dim]" if note else ""
        lines.append(f"  [b]{format_key(binding.key):<8}[/b] {binding.description}{suffix}")
    return lines


# ===== Confirmação de descarte (T12-R4) =====

CONFIRM_DISCARD_TITLE = "Descartar download"
CONFIRM_DISCARD_HINT = "y/Enter confirma · n/Esc cancela"
CONFIRM_DISCARD_CANCELLED = "Descarte cancelado"


def confirm_discard_message(title: str) -> str:
    """Texto do modal de descarte (tecla d) — o que será removido e o que não."""
    return (
        f"{CONFIRM_DISCARD_TITLE}: {title}\n\n"
        "Os arquivos parciais desta tarefa serão removidos; o arquivo final já\n"
        "publicado (se existir) e a biblioteca não são tocados.\n\n"
        f"{CONFIRM_DISCARD_HINT}"
    )


# ===== Erros em 3 partes (T12-R5) =====

#: Marcadores estáveis das partes 2 e 3 (a parte 1 abre a mensagem).
ERROR_PRESERVED_MARK = "🛡️"
ERROR_NEXT_MARK = "▶"

#: Parte 2 padrão de quem falha sem tocar em dado local (busca/álbum).
NOTHING_LOST = "Nada foi baixado nem apagado."


def format_error(what: str, *, preserved: str = NOTHING_LOST, next_action: str) -> str:
    """Erro em 3 partes para a UI (T12-R5).

    (1) **O que falhou** (``what``, já com o contexto — ex.: "Erro na busca");
    (2) o que foi **preservado** (o que o usuário não perdeu);
    (3) a **próxima ação** sugerida.

    O detalhe passa por ``mask_diagnostics`` (§8.3: caminho do HOME e URLs
    com credenciais), nunca carrega traceback e o retorno é **texto puro**:
    telas com markup escapam antes de exibir; o toast (``markup=False``) usa
    como está.
    """
    detail = mask_diagnostics(" ".join(what.split()))
    return f"{detail}\n{ERROR_PRESERVED_MARK} {preserved}\n{ERROR_NEXT_MARK} {next_action}"


def escape_markup(text: str) -> str:
    """Escapa ``[...]`` para o markup do Textual (``Content.from_markup``).

    O parser do Textual **remove** tags que não conhece — ``[LIVE]`` sumiria
    da tela. O ``rich.markup.escape`` não resolve sozinho: só escapa padrões
    de tag do Rich e deixa ``[LIVE]`` passar (achado do T12-R6). Aqui todo
    ``[`` vira ``\\[``; o ``]`` não precisa de escape.
    """
    return text.replace("[", "\\[")


def search_error(
    detail: str, *, next_action: str = "Tente de novo em instantes ou mude o termo buscado."
) -> str:
    """Falha da busca em 3 partes (T12-R5)."""
    return format_error(f"{SEARCH_ERROR_TITLE}: {detail}", next_action=next_action)


def album_error(detail: str) -> str:
    """Falha ao carregar o álbum em 3 partes (T12-R5)."""
    return format_error(
        f"{ALBUM_ERROR_TITLE}: {detail}",
        next_action="Escolha outro álbum ou abra este de novo.",
    )


def player_error(message: str) -> str:
    """Falha do player em 3 partes (T12-R5)."""
    return format_error(
        f"Erro no player: {message}",
        preserved="A biblioteca e os downloads não foram afetados.",
        next_action="Escolha outra faixa ou tente de novo.",
    )


def download_failed(error: str) -> str:
    """Falha de download em 3 partes (T12-R5; unifica o aviso do T11)."""
    return format_error(
        f"❌ Falha no download: {error}",
        preserved="Nada foi publicado na biblioteca (temporários da tarefa foram limpos).",
        next_action="Veja o detalhe no log ou tente baixar de novo (Ctrl+D).",
    )


# ===== Toasts e mensagens de estado (T12-R7 · ponto único pt-BR) =====

# --- Reprodução ---
NO_TRACK_PLAYING = "Nenhuma faixa em reprodução"
TRACK_WITHOUT_VIDEO_ID = "Faixa sem videoId; não é possível reproduzir"


def play_started(title: str) -> str:
    return f"▶ {title}"


def playback_error(detail: str) -> str:
    return f"⚠️ {detail}"


# --- Busca e aba 💿 Álbum ---
SEARCH_STARTING = "Buscando..."
SEARCH_NO_RESULTS = "Nenhum resultado encontrado."
SEARCH_ERROR_TITLE = "Erro na busca"
SEARCH_INIT_DETAIL = "não foi possível inicializar a conexão com o YouTube Music"
SEARCH_INIT_NEXT = "Verifique a rede e tente de novo."
ALBUM_LOADING = "Carregando álbum..."
ALBUM_EMPTY = "Álbum sem faixas disponíveis."
ALBUM_ERROR_TITLE = "Erro ao carregar álbum"
ALBUM_WITHOUT_ID = "Álbum sem identificador; não é possível abrir"
ALBUM_REQUIRED = "Nenhum álbum aberto — selecione um álbum na busca"

# --- Downloads ---
RETRY_REQUIRES_INTERRUPTED = "Selecione um download interrompido (⏸️) na aba downloads para retomar"
DISCARD_REQUIRES_INTERRUPTED = (
    "Selecione um download interrompido (⏸️) na aba downloads para descartar"
)
RETRY_FAILED = "Não foi possível retomar o download"
DISCARD_FAILED = "Não foi possível descartar o download"
ALBUM_NO_TRACKS = "Álbum sem faixas para baixar"
ALBUM_NOTHING_NEW = "nada novo para baixar"


def retrying_download(title: str) -> str:
    return f"🔁 Retomando: {title}"


def discarded_download(title: str, removed: int) -> str:
    detail = f" — {removed} temporário(s) removido(s)" if removed else ""
    return f"🗑️ Download descartado: {title}{detail}"


def interrupted_downloads(count: int) -> str:
    return (
        f"⏸️ {count} download(s) interrompido(s) — aba 📥: "
        "selecione e use r (retomar) ou d (descartar)"
    )


def album_queue_summary(album: str, parts: list[str]) -> str:
    return f"💿 {album}: {', '.join(parts) if parts else ALBUM_NOTHING_NEW}"


def album_queued(count: int) -> str:
    return f"{count} faixa(s) enfileirada(s)"


def album_already_running(count: int) -> str:
    return f"{count} já em andamento"


def album_skipped(count: int) -> str:
    return f"{count} já baixada(s)"


def already_queued(title: str) -> str:
    return f"⏳ Já na fila: {title}"


def download_starting(title: str) -> str:
    return f"Iniciando download: {title}"


def download_completed(title: str) -> str:
    return f"✅ Download concluído: {title}"


def download_existing_adopted(title: str) -> str:
    """Desfecho Q2(a)/R8: arquivo do destino já existia — registrado sem baixar."""
    return f"♻️ Já existia — validado e registrado sem baixar: {title}"


#: Rótulo curto do mesmo desfecho na tabela de downloads (coluna Desfecho).
DOWNLOAD_EXISTING_ADOPTED = "Já existia"


def download_cancelled(title: str) -> str:
    return f"Download cancelado: {title}"


# --- Biblioteca local ---
LIBRARY_EMPTY_TEXT = (
    "Nenhuma faixa na biblioteca local ainda.\nBaixe músicas com Ctrl+D durante a reprodução."
)
LIBRARY_LOADING_TEXT = "Carregando biblioteca local..."
LIBRARY_UNAVAILABLE_TOAST = "⚠️ Falha ao carregar a biblioteca local — detalhes no log"
LIBRARY_LIKE_NEEDS_DOWNLOAD = "Baixe a faixa primeiro (Ctrl+D) para curtí-la na biblioteca local"
LIKE_ADDED = "❤️ Curtida (local)"
LIKE_REMOVED = "Removida das curtidas (local)"

# --- Playlists locais (T17.1/R3) ---
PLAYLISTS_EMPTY_TEXT = "Nenhuma playlist ainda.\nUse N para criar a primeira."
PLAYLISTS_LOADING_TEXT = "Carregando playlists..."
PLAYLIST_LOADING_TEXT = "Carregando playlist..."
PLAYLIST_EMPTY_TEXT = "Playlist vazia.\nNa Biblioteca, selecione faixas e use p para adicionar."
PLAYLIST_NEW_PROMPT = "Nova playlist — nome:"
PLAYLIST_RENAME_PROMPT = "Renomear playlist — novo nome:"
PLAYLIST_NAME_PLACEHOLDER = "Nome da playlist"
PLAYLIST_NAME_REQUIRED = "O nome não pode ficar vazio."
PLAYLIST_RENAME_FAILED = "Não foi possível renomear — a playlist sumiu ou é de sistema."
PLAYLIST_DELETE_FAILED = "Não foi possível excluir — a playlist sumiu ou é de sistema."
PLAYLIST_REORDER_FAILED = "Não foi possível reordenar a playlist."
PLAYLISTS_UNAVAILABLE_TOAST = "⚠️ Falha ao carregar as playlists — detalhes no log"


def playlist_label(name: str, track_count: int) -> str:
    """Linha do nível 1 da aba Playlists: ``nome · N faixas`` (R3)."""
    count = "1 faixa" if track_count == 1 else f"{track_count} faixas"
    return f"{name} · {count}"


def playlist_created(name: str) -> str:
    return f"🎵 Playlist criada: {name}"


def playlist_renamed(name: str) -> str:
    return f"🎵 Playlist renomeada: {name}"


def playlist_deleted(name: str) -> str:
    return f"🗑 Playlist excluída: {name}"


def confirm_delete_playlist(name: str, track_count: int) -> str:
    count = "1 entrada" if track_count == 1 else f"{track_count} entradas"
    return f"Excluir a playlist «{name}» e suas {count}? As faixas continuam na biblioteca."


def playlist_track_removed(title: str) -> str:
    return f"Faixa removida da playlist: {title}"


def playlist_track_moved(title: str, *, up: bool) -> str:
    direction = "cima" if up else "baixo"
    return f"Movida para {direction}: {title}"


def playlist_move_edge(*, up: bool) -> str:
    return "Já está no topo da playlist." if up else "Já está no fim da playlist."


def playlists_unavailable(reason: str, log_path: str) -> str:
    """Falha de leitura das playlists — nunca parecer "nenhuma playlist" (R3)."""
    return (
        "[b]⚠️ Não foi possível carregar as playlists[/b]\n"
        f"{reason}\n"
        f"Nada foi apagado. Detalhes no log: {log_path}"
    )


# --- Adicionar à playlist (T17.1/R4/R5) ---
PLAYLIST_PICKER_TITLE_TRACK = "➕ Adicionar faixa — escolha a playlist:"
PLAYLIST_PICKER_TITLE_ALBUM = "➕ Adicionar álbum — escolha a playlist:"
PLAYLIST_PICKER_NEW = "➕ Nova playlist…"
PLAYLIST_PICKER_EMPTY = "Nenhuma playlist ainda — '➕ Nova playlist…' cria a primeira."
PLAYLIST_ADD_NEEDS_DOWNLOAD = "Baixe a faixa primeiro (Ctrl+D) para adicioná-la a uma playlist."
PLAYLIST_ADD_FAILED = "Não foi possível adicionar — a playlist mudou; tente de novo."
ALBUM_PLAYLIST_HINT = (
    "Nenhum álbum alvo — destaque um álbum na Biblioteca (ou abra com Enter) e tente de novo."
)


def playlist_track_added(title: str, playlist: str) -> str:
    return f"➕ {title} → {playlist}"


def playlist_track_already(title: str, playlist: str) -> str:
    return f"Já estava em «{playlist}»: {title}"


def album_playlist_summary(album_title: str, added: int, already: int, missing: int | None) -> str:
    """Recado honesto do álbum → playlist (R5): só o que aconteceu de fato.

    ``missing`` só entra quando o total do álbum é conhecido (``track_count``
    do modelo); o que não deu para adicionar aparece com o caminho de saída
    (baixar). Nada é arredondado para "tudo certo".
    """
    parts = [
        "1 adicionada" if added == 1 else f"{added} adicionadas",
        "1 já estava" if already == 1 else f"{already} já estavam",
    ]
    if missing:
        parts.append(
            "1 fora da biblioteca (baixe para incluí-la)"
            if missing == 1
            else f"{missing} fora da biblioteca (baixe para incluí-las)"
        )
    return f"➕ Álbum «{album_title}»: " + " · ".join(parts)


def album_playlist_nothing(album_title: str, missing: int | None) -> str:
    """Nada a adicionar — mensagem clara em vez de um toast silencioso (R5)."""
    if missing:
        detail = (
            "1 faixa fora da biblioteca (baixe para incluí-la)."
            if missing == 1
            else f"{missing} faixas fora da biblioteca (baixe para incluí-las)."
        )
        return f"Nada a adicionar de «{album_title}» — {detail}"
    return f"Nada a adicionar de «{album_title}»: nenhuma faixa na biblioteca local."


#: Marca de faixa ausente nas listas — legenda honesta junto da lista (R7).
MISSING_MARK = "⚠"
MISSING_LEGEND_TEXT = f"{MISSING_MARK} arquivo não encontrado na última reconciliação"
SINGLES_SECTION_TEXT = "— Faixas avulsas —"


# --- Downloads e player (estados §12.2) ---
DOWNLOADS_EMPTY_TEXT = (
    "Nenhum download ainda.\nUse Ctrl+D para baixar a faixa atual ou A para o álbum aberto."
)
PLAYER_LOADING_TEXT = "carregando…"
PLAYER_ERROR_TEXT = "falha"


def library_unavailable(reason: str, log_path: str) -> str:
    """Falha de leitura da biblioteca — nunca parecer "vazia" (T05-R4)."""
    return (
        "[b]⚠️ Não foi possível carregar a biblioteca local[/b]\n"
        f"{reason}\n"
        f"Nada foi apagado. Detalhes no log: {log_path}"
    )


def reconciliation_summary(summary: str) -> str:
    return f"🧹 Reconciliação: {summary}"


# --- Config ---


def audio_quality_set(value: str) -> str:
    return f"Qualidade de áudio: {value} (aplica na próxima faixa)"


def download_format_set(value: str) -> str:
    return f"Formato de download: {value}"


def volume_set(volume: int) -> str:
    return f"Volume: {volume}%"


def download_dir_set(path: object) -> str:
    return f"Diretório de downloads: {path}"


def download_quality_set(value: str) -> str:
    return f"Qualidade do download: {value or '320'}"


def concurrent_set(concurrent: int) -> str:
    return f"Downloads simultâneos: {concurrent}"


INVALID_VALUE = "Valor inválido"


def download_dir_error(detail: str) -> str:
    return f"Não foi possível usar o diretório: {detail}"


def config_save_error(detail: str) -> str:
    """Erro ao salvar a Config em 3 partes (§12.2/T12-R5)."""
    return format_error(
        f"Não foi possível salvar a configuração: {detail}",
        preserved=(
            "A alteração vale agora, mas não foi gravada — o valor anterior volta ao reabrir."
        ),
        next_action="Verifique permissões e espaço do arquivo de configuração.",
    )
