"""Pacote ytmusic_tui.

Este pacote não reexporta instâncias (``player``, ``library`` etc.) para
evitar que ``ytmusic_tui.player`` seja sombreado pela instância do player.
Importe sempre dos submódulos, por exemplo::

    from ytmusic_tui.player import player
"""

__version__ = "0.3.2"
