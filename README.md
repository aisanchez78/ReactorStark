# Monitorización del reactor experimental con ThingSpeak

Aplicación de consola en Python para consultar un canal ThingSpeak, escribir una
medición manual y cargar las diez muestras incluidas en la actividad. No necesita
paquetes externos; usa la biblioteca estándar de Python.

## Requisitos

- Python 3.10 o posterior.
- Una cuenta de ThingSpeak y un canal creado para la actividad.
- El identificador del canal, la clave de escritura y la clave de lectura.

## Configuración

1. Cree en ThingSpeak un canal público y habilite cinco campos, en este orden:
   carga del núcleo, estabilidad, flujo de refrigerante, latencia de JARVIS y
   consumo de repulsores.
2. Copie `.env.example` a `.env` y sustituya los valores de ejemplo por el
   identificador y las claves de su canal. `.env` está excluido de Git; no lo
   publique ni lo incluya en la entrega si contiene claves reales.
3. La aplicación carga automáticamente las variables desde `.env` al ejecutar
   `read`, `write`, `samples` o `web`. Las variables ya definidas en el entorno
   tienen prioridad sobre las del archivo.

La aplicación nunca guarda las claves en el código fuente ni las imprime.

## Ejecución

Desde esta carpeta:

```sh
python3 main.py --help
python3 main.py preview
python3 main.py write --field1 52.4 --field2 96.8 --field3 35.2 --field4 118.6 --field5 42.3
python3 main.py read --results 10
python3 main.py samples
python3 main.py web
```

`write` añade una sola medición y `read` consulta las mediciones recientes. `samples`
envía las diez filas de la actividad, una cada 15 segundos (aproximadamente 135
segundos en total). El intervalo no puede reducirse por debajo de 15 segundos para
evitar que la cuenta gratuita rechace escrituras consecutivas. `preview` permite
revisar los valores sin requerir conexión ni claves.

`web` inicia un panel local en `http://127.0.0.1:8000`. Desde ahí se puede consultar
y refrescar el historial, elegir cuántas lecturas mostrar y enviar una medición con
los cinco campos. Se muestra un gráfico histórico independiente para cada campo,
con las lecturas del periodo seleccionado. El panel requiere las claves de lectura
y escritura en `.env`; estas permanecen en el servidor local y no se envían al navegador.

Los campos 1–3 se validan en el rango 0–100; latencia y consumo deben ser no
negativos. La lectura acepta entre 1 y 8000 resultados. Los errores de conexión,
configuración, claves y respuestas de la API se comunican en la terminal.

## Pruebas

```sh
python3 -m unittest discover -s tests -v
```

Las pruebas usan respuestas HTTP simuladas: comprueban el formato de lectura y
escritura sin enviar datos ni necesitar las claves reales. La comprobación de
ThingSpeak en producción debe hacerse con el canal del grupo.

## Entrega

Incluya este directorio en el ZIP de entrega, junto con la hoja de control grupal
si el profesorado la ha proporcionado. Antes de compartirlo, revise que no contiene
un `.env` con claves reales.
