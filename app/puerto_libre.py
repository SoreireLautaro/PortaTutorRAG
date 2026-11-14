"""Imprime puertos TCP libres en 127.0.0.1, uno por argumento preferido.

Uso:  python app/puerto_libre.py 8080 8081 7860

Para cada puerto preferido imprime ese si está libre; si no, uno efímero
elegido por el sistema. Nunca repite un puerto ni uno de los preferidos.
Lo usan iniciar.bat / iniciar.sh para que la app levante aunque los
puertos habituales estén ocupados (p. ej. 8081 por un Apache).
"""
import socket
import sys


def main() -> None:
    preferidos = [int(a) for a in sys.argv[1:]]
    forbidden = set(preferidos)
    elegidos: list[int] = []
    for pref in preferidos:
        puerto = 0
        if pref not in elegidos:
            s = socket.socket()
            try:
                s.bind(("127.0.0.1", pref))
            except OSError:
                pass
            else:
                puerto = pref
            s.close()
        while puerto == 0:
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            p = s.getsockname()[1]
            s.close()
            if p not in elegidos and p not in forbidden:
                puerto = p
        elegidos.append(puerto)
    print(" ".join(map(str, elegidos)))


if __name__ == "__main__":
    main()
