<div align="center">

[English](README.md) · [Français](README.fr.md) · **Español** · [Deutsch](README.de.md)

# Mas

**Tu servidor, cartografiado y desplegado.**
Panel autoalojado que adopta lo que ya funciona en tu máquina Debian + nginx, despliega desde GitHub en cada push,
ejecuta servicios listos para usar y reparte tus apps entre varios servidores.

![estado: beta](https://img.shields.io/badge/status-beta-8b5cf6)
![licencia: MIT](https://img.shields.io/badge/license-MIT-0ea5e9)
![python 3.11+](https://img.shields.io/badge/python-3.11%2B-22c55e)
![Debian + nginx](https://img.shields.io/badge/Debian%20%2B%20nginx-supported-f59e0b)

![Panel de Mas: una app con su despliegue de producción y el registro de compilación en directo](docs/app.jpg)

</div>

**Mas cartografía el servidor que ya tienes.** Apúntalo a una máquina Debian + nginx que lleva
años funcionando, con una mezcla de sitios PHP, apps Node bajo PM2, algunas unidades systemd y
uno o dos contenedores Docker, y te dice qué se está ejecutando, dónde, para qué dominio, con
qué usuario y desde qué repositorio git. No se reinstala ni se rompe nada.

Después gestionas esos servicios desde la CLI `mas` o desde un panel web protegido por una
contraseña y autenticación en dos pasos, vigilas los certificados y nginx, enlazas dominios
de tu registrador (OVH, Cloudflare, Gandi, Hetzner, DigitalOcean, Porkbun) y despliegas
proyectos desde GitHub en cada push, al estilo de Vercel, en la misma máquina. Cuando un servidor
no basta, otros servidores se unen como agentes y ejecutan copias de tus apps detrás del mismo balanceador de carga.

## De un vistazo

| | |
|---|---|
| 🔎 **Descubrimiento** | vhosts de nginx, puertos, `/proc`, systemd, PM2 y Docker leídos en una sola lista de servicios: dominio, usuario, carpeta, repositorio git. Solo lectura, sin IA. |
| 🚀 **Despliegue desde GitHub** | Releases atómicas en cada push, unidad systemd y vhost de nginx generados, Let's Encrypt, comprobación de salud, vuelta atrás automática, registros de compilación en directo. |
| 🌐 **Dominios** | Registros DNS creados en OVH, Cloudflare, Gandi, Hetzner, DigitalOcean o Porkbun, y después HTTPS. |
| 🗄️ **Supabase** | La pila oficial autoalojada en un clic, apps conectadas desde su repositorio, migraciones al desplegar. |
| 📦 **Servicios listos para usar** | Servidor de correo, Minecraft, Uptime Kuma, Vaultwarden, n8n, Gitea o cualquier imagen Docker, con copias de seguridad cada noche. |
| ✏️ **CMS** | Tus clientes editan los textos, imágenes y artículos de su sitio; cada guardado es un commit y un despliegue. |
| 📊 **Tráfico** | Peticiones, páginas vistas y visitantes por día para cada sitio, a partir de los registros de nginx, sin cookies. |
| 🧠 **Recursos** | Memoria y CPU de todo en una sola página; límites de memoria, reposo en inactividad, instancias que siguen el tráfico, consejos aplicados en un clic. |
| 🛠️ **Operaciones** | Comprobaciones de salud con soluciones, reinicio idéntico de todos los servicios, páginas de mantenimiento, volver a desplegarlo todo. |
| 🖧 **Clúster** | Agentes sin panel, copias de apps en varios servidores, mapa en vivo, conmutación por error automática, promoción. |

## Contenido

- [¿Por qué otro más?](#por-qué-otro-más) · [Instalación](#instalación) · [CLI](#cli) · [Panel](#panel)
- [Desplegar desde GitHub](#desplegar-desde-github) · [Dominios](#dominios-ovh-cloudflare-gandi-hetzner-digitalocean-porkbun) ·
  [Supabase](#supabase-autoalojado) · [Servicios listos para usar](#servicios-listos-para-usar) · [CMS](#cms-para-tus-clientes)
- [Administrar el servidor](#administrar-el-servidor): [recursos](#recursos), [tráfico](#estadísticas-de-tráfico),
  [mantenimiento](#modo-mantenimiento), [reiniciar todo](#reiniciar-todo-idéntico), [comprobaciones de salud](#comprobaciones-de-salud)
- [Varios servidores](#varios-servidores-clúster) · [Cómo funciona la detección](#cómo-funciona-la-detección) ·
  [Modelo de seguridad](#modelo-de-seguridad) · [Desarrollo](#desarrollo) · [Hoja de ruta](#hoja-de-ruta)

> [!WARNING]
> **Beta.** Mas se ejecuta como root y modifica nginx, systemd y el DNS. Cada cambio se respalda y se
> valida (`nginx -t`) y el descubrimiento es de solo lectura, pero pruébalo primero en un servidor que
> puedas restaurar y lee el [modelo de seguridad](#modelo-de-seguridad). Los comentarios y las issues son muy bienvenidos.

## ¿Por qué otro más?

Coolify, Dokploy y CapRover son estupendos, pero quieren un servidor limpio: todo tiene que
ejecutarse en sus contenedores, detrás de su proxy. Si tu servidor ya aloja diez sitios
configurados a mano, adoptar uno de ellos significa migrarlo todo.

Mas empieza por el otro extremo:

|                         | Coolify / Dokploy / CapRover | Mas                                   |
|-------------------------|------------------------------|---------------------------------------|
| Punto de partida        | Servidor nuevo               | Tu servidor existente                 |
| Proxy inverso           | Traefik / Caddy (el suyo)    | Tu nginx, tus vhosts                  |
| Entornos de ejecución   | Solo Docker                  | PHP-FPM, systemd, PM2, Docker, estático |
| Apps existentes         | Hay que volver a desplegarlas | Detectadas y adoptadas donde están   |
| Detección               | n/a                          | Análisis determinista, sin IA         |
| Apps inactivas          | Siempre en marcha            | Pueden dormir y despertar con la siguiente visita |

Toda la detección es determinista: análisis de archivos de configuración, `/proc`, `ss`, `systemctl`, `pm2 jlist`
y el socket de Docker. No interviene ningún modelo de IA en ninguna parte, y el descubrimiento no necesita ninguna API externa
(solo las integraciones opcionales de GitHub y de los proveedores DNS hablan con sus propias API).

## Instalación

En el servidor (Debian 12+, como root):

```console
$ git clone https://github.com/SyanCode/Mas.git /opt/mas-install
$ cd /opt/mas-install
$ sudo ./deploy/install.sh --domain mas.example.com --email you@example.com
```

Sin `--domain` el panel sigue siendo privado (túnel SSH, ver más abajo). Actualizaciones:
`sudo mas self-update` (git pull, reinstalación, recompilación, reinicio).

El instalador es idempotente y pide dos cosas:

1. **La contraseña de administrador y después la 2FA**: se muestra un código QR en el terminal; escanéalo con
   Google Authenticator, Aegis, 1Password, Bitwarden… y escribe el código de 6 dígitos.
2. **El dominio del panel** (p. ej. `mas.example.com`, su DNS debe apuntar al servidor). Mas obtiene
   un certificado de Let's Encrypt e instala un vhost HTTPS. Déjalo vacío para mantener el panel
   privado y acceder a él mediante un túnel SSH:
   `ssh -L 7070:127.0.0.1:7070 you@server`, y luego abre http://localhost:7070.

Sin interacción: `sudo ./deploy/install.sh --domain mas.example.com --email you@example.com`.

Lo que se instala: `/opt/mas/src` (código fuente), `/opt/mas/venv`, `/etc/mas/config.toml`,
`/var/lib/mas/mas.db`, `mas.service`, `/etc/nginx/sites-available/mas.conf` y `/usr/local/bin/mas`.

### Actualizar

```console
$ sudo mas self-update                       # follow the published branch (git checkout)
$ sudo mas self-update --from mas.tar.gz     # from a `git archive` tarball
```

Actualiza el código fuente, reinstala, recompila el panel y reinicia `mas.service`. Un checkout de
git se mueve a `origin/<branch>` aunque esa rama se haya forzado con force-push; se niega a tocar un
checkout con cambios locales. Una instalación desde un archivo conserva el código anterior como
`/opt/mas/src.mas-bak-<date>`.

Cuando se publica una versión más reciente, cada comando `sudo mas …` empieza con un recordatorio de una línea
para ejecutar `sudo mas self-update` (se comprueba en GitHub como mucho cada 6 horas, con 2 s de tiempo de espera, y en silencio
sin conexión; `MAS_NO_UPDATE_CHECK=1` lo desactiva).

## CLI

```console
$ sudo mas scan                 # table of every detected service (--json available)
$ sudo mas logs blog.example.com -n 200
$ sudo mas restart api          # asks for confirmation (-y to skip)
$ sudo mas start|stop|reload <service>
$ sudo mas nginx reload         # nginx -t first, reload only if valid
$ sudo mas history              # who did what, when, with what result

$ sudo mas user create [name]   # password + 2FA QR code
$ sudo mas user 2fa [name]      # set up / replace the authenticator app
$ sudo mas user 2fa-reset [name]  # lost phone: disable 2FA (root only)
$ sudo mas user passwd [name]
```

`<service>` es el id que muestra `mas scan` (normalmente el dominio), o cualquier parte no ambigua de él.

| Gestor         | Acciones                       | Comando                                           |
|----------------|--------------------------------|---------------------------------------------------|
| `systemd`      | start, stop, restart, reload   | `systemctl <action> <unit>`                       |
| `pm2`          | start, stop, restart, reload   | `runuser -u <owner> -- pm2 <action> <app>`        |
| `docker`       | start, stop, restart           | API de Docker `POST /containers/<id>/<action>`    |
| `php-fpm`      | reload, restart                | `systemctl <action> php*-fpm.service`             |
| `nginx-static` | reload                         | `nginx -t && systemctl reload nginx`              |

## Panel

![Resumen: todos los servicios detectados en el servidor](docs/overview.jpg)

Pulsa <kbd>⌘K</kbd> / <kbd>Ctrl</kbd>+<kbd>K</kbd> en cualquier lugar para saltar a un servicio, una app o una página.

El panel habla **inglés, francés, español y alemán**: por defecto se usa el idioma del navegador,
y el selector del menú de usuario (y de la página de inicio de sesión) recuerda tu elección. El
texto en inglés es la clave (`t("Deploy Supabase")`), las traducciones están en `web/locales/<lang>.json`,
y `tests/test_i18n.py` falla si un texto no tiene traducción. Añadir un idioma es un archivo JSON
más una línea en `web/lib/i18n.tsx`.

| Página | Qué muestra |
|---|---|
| **Resumen** | todos los servicios detectados: estado, dominios, tipo, gestor, puerto, caducidad del SSL (en naranja a menos de 14 días), repositorio de GitHub, la app de Mas a la que pertenece; filtros y búsqueda; **Reiniciar todo**, **Recargar nginx** |
| **Servicio** | detalles, memoria / CPU / tiempo activo / puertos y enlaces en directo, registros, acciones (con confirmación), tráfico de su sitio, historial, nombre visible y notas, **Sustituir por una app de Mas** |
| **Apps** | apps desplegadas desde GitHub y servicios del catálogo; **Volver a desplegar todo**, **Mantenimiento** |
| **App** | despliegues con registros de compilación en directo, vuelta atrás, dominio, tráfico, recursos, balanceo de carga, mantenimiento, base de datos, CMS, variables de entorno |
| **Bases de datos** | el Supabase autoalojado: componentes, memoria por contenedor, acceso a Studio, copias de seguridad |
| **Recursos** | memoria y CPU de todo lo que hay en el servidor, límites, reposo, instancias, consejos |
| **Servidores** | el clúster: este servidor, los demás, tokens de unión, mapa en vivo de las peticiones |
| **Salud** | comprobaciones de certificados, certbot, nginx y DNS, con las soluciones |
| **Actividad** | el registro de auditoría: quién hizo qué, cuándo y con qué resultado |
| **Ajustes** | 2FA, contraseña, GitHub App, proveedores DNS, personas (editores del CMS e invitaciones) |

Tema claro y oscuro (sigue al sistema por defecto).

## Desplegar desde GitHub

### 1. Conectar GitHub (una sola vez)

En **Ajustes → GitHub**, haz clic en **Crear la GitHub App** (opcionalmente para una organización). GitHub
muestra la App que se va a crear; confirma, y GitHub devuelve sus credenciales a Mas mediante el
[flujo de manifiesto](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest):
App ID, clave privada (guardada en `/etc/mas/github-app.pem`, `0600`) y secreto del webhook (guardado en
`/etc/mas/config.toml`, `0600`). Después haz clic en **Instalar** y elige los repositorios que Mas puede leer.

La App es privada y pide: **Contents: read and write** (la escritura solo la usa el CMS, para
hacer commit de las ediciones de contenido), **Metadata: read**, **Pull requests: read**, y los eventos de webhook **push** y
**pull_request**. Una App creada antes del CMS tiene Contents en solo lectura: cámbialo
a *Read and write* en los ajustes de la App en GitHub y después acepta los nuevos permisos en la
instalación.

<details>
<summary>Crear la App a mano</summary>

1. github.com → Settings → Developer settings → GitHub Apps → **New GitHub App**.
2. Homepage URL: `https://<your dashboard>`; Webhook URL: `https://<your dashboard>/api/github/webhook`;
   Webhook secret: una cadena aleatoria larga.
3. Permisos: Contents read and write, Metadata read-only, Pull requests read-only. Eventos: Push, Pull request.
4. "Only on this account", créala y después **Generate a private key** (descarga un `.pem`).
5. En el servidor: `sudo mas github setup` (pide el App ID, el slug, la ruta del `.pem` y el secreto), después
   `sudo systemctl restart mas`, e instala la App en tus repositorios.
</details>

### 2. Crear una app

**Apps → Nueva app**: elige un repositorio y una rama. Mas detecta el tipo (Next.js, Node, Python,
estático, PHP) a partir del repositorio; indica un dominio opcional y variables de entorno (guardadas
cifradas con una clave en `/etc/mas/secret.key`). Crea: el primer despliegue empieza y su registro
se muestra en directo en el panel.

A partir de ahí, cada push a la rama despliega automáticamente:

1. `git clone` del commit enviado en `/srv/mas/apps/<app>/releases/<sha>/` (el token de la
   instalación se pasa a git por el entorno, nunca en la línea de comandos ni en los registros)
2. compilación con el usuario dedicado `mas-<app>` (`npm ci && npm run build`, un venv de Python +
   `pip install`, `composer install`, o tu propio comando de compilación)
3. cambio atómico del enlace simbólico `current` (una app Next.js compilada con `output: 'export'` no tiene
   servidor: Mas sirve su carpeta `out/` con nginx y omite la unidad systemd)
4. reinicio de `mas-app-<app>.service` (unidad systemd generada, usuario no root, `NoNewPrivileges`,
   `ProtectSystem`, `ProtectHome`, app escuchando en `127.0.0.1:<port>`)
5. vhost de nginx generado a partir de una plantilla (copia de seguridad, `nginx -t`, recarga; se rechaza si otro vhost
   ya sirve el dominio)
6. `certbot certonly --nginx` si el dominio aún no tiene certificado, y después vhost HTTPS
7. comprobación de salud HTTP; **si falla, la release anterior se restaura automáticamente**

Se conservan las 5 últimas releases; **Volver** restaura cualquiera de ellas en un clic (`mas rollback <app>`).

### Dominios (OVH, Cloudflare, Gandi, Hetzner, DigitalOcean, Porkbun)

Mas puede crear los registros DNS por sí mismo. En **Ajustes → Dominios**:

1. Indica la **IP pública** de este servidor (Mas sugiere la dirección a la que apuntan tus dominios existentes).
2. Conecta uno o varios registradores. Cada tarjeta enlaza a la página de tokens del proveedor y enumera los
   derechos exactos que hay que conceder (leer zonas, crear/actualizar registros; Mas nunca borra un registro). Las claves se
   verifican con el proveedor y después se guardan cifradas.

| Proveedor | Credenciales |
|----------|-------------|
| OVHcloud | application key + secret + consumer key (página de token con los derechos ya rellenados) |
| Cloudflare | API token, plantilla “Edit zone DNS” (los registros se crean como solo DNS, nube gris) |
| Gandi | Personal access token (LiveDNS) |
| Hetzner DNS | API token |
| DigitalOcean | API token con el scope `domain` |
| Porkbun | API key + secret (activa el acceso a la API en cada dominio) |

Namecheap no está soportado: su API necesita una IP en lista blanca y reescribe toda la zona en cada cambio.

Después, en una app (o en *Nueva app*), elige uno de tus dominios y un subdominio (por defecto, el nombre de la
app). Mas comprueba el nombre en directo (libre, ya apunta aquí, apunta a otro sitio, CNAME,
ya en uso), crea el registro `A` (y `AAAA`), espera a que resuelva y después despliega con el
sitio de nginx y el certificado HTTPS. Un registro que apunta a otro sitio solo se sustituye tras una
confirmación explícita; un CNAME nunca se toca.

CLI: `mas dns connect <provider>`, `mas dns ip <ipv4> [--ipv6 …]`, `mas dns zones`,
`mas domain <app> <sub.example.com> [--overwrite | --no-dns]`.

### Supabase autoalojado

**Bases de datos → Desplegar Supabase** instala la pila docker oficial de Supabase (Postgres, Auth,
REST, Realtime, Storage, Studio) en el servidor:

- cada puerto publicado se vincula a `127.0.0.1` (de lo contrario, Docker se saltaría el cortafuegos);
- solo la API pública (`/rest`, `/auth`, `/realtime`, `/storage`, `/functions`, `/graphql`) se
  publica a través de nginx, en el dominio que elijas (registro DNS creado igual que para las apps), con HTTPS;
- **Studio sigue siendo privado**: en el dominio de Supabase solo responde a la red local y a las
  direcciones que autorices (página Bases de datos), detrás de su inicio de sesión; si no, a través del túnel SSH que muestra Mas;
- los secretos (contraseña de Postgres, secreto JWT, claves `anon` / `service_role`) los genera Mas y
  se guardan cifrados;
- cada día se ejecuta un `pg_dump`, y se conservan los 7 últimos en `/var/lib/mas/backups/supabase`.

Una instancia por servidor (todas las apps conectadas comparten su base de datos); la pila necesita Docker con
el plugin compose.

**Qué instalar.** Elige lo que necesitan tus apps; Mas sugiere la opción que encaja con la memoria libre:

| Opción | Qué pueden hacer las apps | ≈ RAM |
|---|---|---|
| **Esencial** | base de datos, cuentas de usuario, API | 400 MB |
| **Recomendado** | + subida de archivos, actualizaciones en directo, panel de administración | 880 MB |
| **Completo** | + redimensionado de imágenes, funciones de servidor, registros, muchas conexiones | 1.8 GB |

**Personalizar** muestra los componentes uno a uno, y la página Bases de datos permite cambiarlos más tarde
(`docker compose up -d --remove-orphans`, los volúmenes de datos se conservan):

| Componente | Nombre en Supabase | Recomendado | ≈ RAM |
|---|---|---|---|
| Base de datos, cuentas de usuario y API (siempre activo) | Postgres, Auth, REST, Kong | activado | 400 MB |
| Panel de administración (privado, se inicia/detiene a demanda) | Studio + pg-meta | activado | 250 MB |
| Actualizaciones en directo | Realtime | activado | 150 MB |
| Subida de archivos | Storage | activado | 80 MB |
| Redimensionado de imágenes | imgproxy | desactivado | 60 MB |
| Funciones de servidor | Edge Functions | desactivado | 150 MB |
| Registros y estadísticas | Logflare + Vector | desactivado | 600 MB |
| Muchas conexiones | Supavisor (desactivado: Postgres se publica directamente en `127.0.0.1`) | desactivado | 150 MB |

#### Conectar una app

La tarjeta **Base de datos** de una app lee su repositorio y se adapta a él:

- **Detecta que la app usa Supabase** por sus librerías (`@supabase/supabase-js`,
  `@supabase/ssr`, `supabase` para Python…), su archivo de entorno de ejemplo o una carpeta `supabase/`. Cuando el
  servidor aún no tiene Supabase, **Crear la base de datos y conectar** lo instala y conecta la app
  en cuanto está en marcha. En **Apps → Nueva app**, una app Supabase se conecta antes de su primera compilación.
- **Conectar la base de datos** escribe la URL y las claves con los nombres que lee el código: los habituales
  (`SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`,
  `DATABASE_URL`…), los del framework (`VITE_…`, `PUBLIC_…` para Astro y SvelteKit, `EXPO_PUBLIC_…`)
  y todas las variables de Supabase de su `.env.example`. Un secreto (clave service_role, URL de Postgres)
  nunca se escribe en una variable que pueda leer el navegador (`NEXT_PUBLIC_`, `VITE_`…): en su lugar, Mas lo indica en una lista.
- **El repositorio describe su base de datos** con la estructura de la CLI de Supabase:
  `supabase/migrations/<timestamp>_<name>.sql` y `supabase/seed.sql`. En cada despliegue de una
  app conectada, Mas ejecuta las migraciones nuevas antes de la compilación, en orden, cada una en una transacción,
  y las registra en `supabase_migrations.schema_migrations` como `supabase db push`. Si una
  falla, el despliegue se detiene y el sitio se queda en su versión actual. El seed se carga una vez,
  cuando lo marcas al conectar. La tarjeta enumera las migraciones, aplicadas o pendientes.
- **Importar mi base de desarrollo** copia el esquema `public` de un proyecto de supabase.com (tablas,
  funciones, políticas, con o sin las filas) y conecta la app. Pega la cadena de conexión *Session pooler*
  (proyecto → **Connect**); se pasa a `pg_dump` por el entorno, nunca
  en una línea de comandos, y no se conserva. Las tablas que ya existen aquí se rechazan salvo que marques
  *sustituirlas*. Mas ejecuta un `pg_dump` tan reciente como el servidor de origen. No se copian: los usuarios
  (`auth`), los archivos (Storage) ni los triggers sobre `auth.users`.

Una app conectada a otro proyecto de Supabase (su `SUPABASE_URL` apunta a otro sitio) se deja tal cual.

Los servicios que quedan están ajustados para un servidor pequeño: Postgres con `shared_buffers=64MB` y menos
memoria de trabajo, un solo worker de Kong con una caché de 32 MB, un heap de Node.js limitado para Studio, Storage y
pg-meta. El archivo original sin modificar se conserva como `docker-compose.original.yml`; la página muestra
la memoria que usa realmente cada contenedor.

Restaurar una copia de seguridad: `docker cp <file> supabase-db:/tmp/r.dump && docker exec supabase-db pg_restore
-U postgres -d postgres --clean /tmp/r.dump`.

### Servicios listos para usar

**Apps → Desplegar un servicio** ejecuta servidores listos para usar con Docker, sin necesidad de repositorio: elige uno, rellena
un formulario corto, y Mas escribe `/srv/mas/stacks/<name>/docker-compose.yml`, abre los puertos, obtiene el
certificado de su dirección y te muestra cómo usarlo.

| Servicio | Qué obtienes |
|---|---|
| **Servidor de Minecraft (Java)** | Paper, Vanilla, Fabric, Forge o Purpur; versión, memoria, modo de juego, jugadores permitidos, operadores |
| **Servidor de Minecraft (Bedrock)** | para móviles, consolas y Windows (UDP 19132) |
| **Servidor de correo** | docker-mailserver: tus buzones `@tudominio` en cualquier app de correo (IMAP 993, SMTP 465/587), clave DKIM creada, los registros DNS que hay que añadir (A, MX, SPF, DKIM, DMARC) enumerados con sus valores, **Añadir un buzón** en un clic |
| **Uptime Kuma**, **Vaultwarden**, **n8n**, **Gitea** | servidos en su propio dominio con HTTPS |
| **Cualquier imagen Docker** | una imagen, su puerto web en un dominio y/o puertos publicados tal cual, su entorno y una carpeta persistente |

- Las páginas web escuchan en `127.0.0.1` y nginx las sirve con HTTPS. Los puertos pensados para el exterior
  (25565, 25, 993…) los publica Docker en todas las interfaces, lo que los abre en el cortafuegos;
  Mas rechaza un puerto que ya usa otro servicio.
- Las contraseñas (primer buzón, token de administración de Vaultwarden) se generan y se guardan cifradas; la
  página del servicio las muestra a demanda, con la dirección a la que conectarse.
- La página del servicio tiene **Iniciar / Detener / Reiniciar**, sus registros y sus ajustes: al guardarlos
  se vuelve a desplegar. **Redesplegar** también descarga la imagen más reciente. Los datos se quedan en `/srv/mas/stacks/<name>/data`
  entre actualizaciones; eliminar el servicio los conserva salvo que marques *eliminar también sus datos*.
- **Copias de seguridad**: cada noche entre las 3 y las 5 de la madrugada (hora del servidor), Mas detiene el servicio, copia su
  carpeta `data` en `/var/lib/mas/backups/stacks/<name>/<name>-<date>.tar.gz` y lo vuelve a iniciar
  (un mundo de Minecraft o un buzón copiados mientras se escriben no se podrían restaurar). Se conservan las 7 últimas
  (`stacks_keep_backups` en `[deploy]`), y sobreviven al servicio. **Hacer copia ahora** y
  **Restaurar** están en la página del servicio; una restauración aparta primero los datos actuales en
  `data.before-restore`.
- Los servidores de Minecraft arrancan con un heap de 512 MB que crece hasta la memoria que elegiste, y la devuelven
  cuando nadie juega.
- Para el correo, pide a tu proveedor de alojamiento el DNS inverso (PTR) del servidor y comprueba que no
  bloquea el puerto 25 saliente.

Los servicios nuevos son una plantilla en `mas/stacks.py` (un archivo compose construido a partir del formulario); después de cambiar
el catálogo, ejecuta `python -m mas.stacks > web/lib/stackTexts.ts` para que sus textos se traduzcan.

### CMS para tus clientes

Aloja los sitios de tus clientes con Mas y deja que cada cliente edite su propio contenido (textos, imágenes, artículos),
sin darle acceso al servidor ni al código. El sitio conserva su diseño: el repositorio
declara lo que se puede editar en **`mas.cms.json`**, el cliente rellena formularios en el panel, y Mas
escribe los valores, como un commit en la rama desplegada (lo que vuelve a desplegar el sitio) o, para las colecciones
guardadas en Supabase, como filas de tabla publicadas al instante.

**→ La referencia completa está en [docs/cms.md](docs/cms.md)**: páginas, colecciones (artículos, noticias…),
tipos de campo, almacenamiento en git o en Supabase, cómo lee el sitio su contenido, y una lista de comprobación.

```json
{
  "$schema": "https://raw.githubusercontent.com/SyanCode/Mas/main/schema/mas.cms.schema.json",
  "pages": [{ "id": "home", "label": "Home page", "file": "content/home.json",
              "fields": [{ "key": "title", "type": "text", "max": 80, "required": true },
                         { "key": "hero", "type": "image" }] }],
  "collections": [{ "id": "articles", "label": "Articles", "dir": "content/articles",
                    "fields": [{ "key": "title" }, { "key": "date", "type": "date" },
                               { "key": "body", "type": "markdown" }] }]
}
```

**Hacer un sitio compatible con Mas.** Con Claude Code, instala una vez el plugin de este repositorio
y después pídelo con tus propias palabras en el proyecto del sitio:

```
/plugin marketplace add SyanCode/Mas
/plugin install mas-cms@mas

Rends ce site compatible Mas
```

Claude mueve el contenido editable a archivos JSON, escribe `mas.cms.json`, conecta el sitio a ellos
sin tocar su diseño y comprueba el resultado con el `check.py` del plugin. A mano: sigue la
[lista de comprobación](docs/cms.md#making-a-site-compatible-checklist) y empieza `mas.cms.json` con la
línea `$schema` de arriba para tener autocompletado en tu editor. La tarjeta **CMS** de una app también puede escribir
`mas.cms.json` a partir de los archivos JSON que ya están en el repositorio.

En **Apps → Nueva app**, un repositorio con un `mas.cms.json` válido muestra una insignia **Compatible con Mas** (o
*mas.cms.json no válido* con el motivo al pasar el ratón). Después:

1. Dale una cuenta a tu cliente, en **Ajustes → Personas**, de una de estas tres formas:
   - **Enlace de invitación** (lo más sencillo): elige los sitios y envía el enlace. Funciona una vez, durante 7 días,
     incluso con el registro cerrado; el cliente elige un nombre de usuario y una contraseña y llega a
     su sitio. Solo se guarda un hash del enlace, y los enlaces pendientes se pueden revocar.
   - **Abrir el registro**: cualquiera puede crear una cuenta en `/signup/`, opcionalmente solo con un
     código de invitación que tú defines. Una cuenta nueva no ve nada hasta que le das un sitio. Los registros están
     limitados a 5 por hora por dirección y se anotan en el registro de auditoría.
   - **Nuevo editor**: tú eliges el nombre de usuario y Mas genera una contraseña que se muestra una sola vez.

   **Sitios**, en cada editor, elige qué apps puede editar; la página de la app tiene el mismo interruptor en
   su tarjeta **CMS**, que además comprueba el esquema (y, para las colecciones de Supabase, crea la tabla).
2. El cliente inicia sesión en el mismo panel y solo ve **Mis sitios** y **Cuenta** (contraseña,
   2FA). Todas las demás rutas de la API responden `403` a un editor.

Qué mantiene el sitio a salvo: el esquema siempre se lee del repositorio (nunca del navegador);
solo se escriben los campos declarados de los archivos (o tablas) declarados, y las claves que el esquema no
conoce se dejan intactas; los valores se comprueban en el servidor (longitud, tipo, opciones permitidas, enlaces
limitados a `https://`, `/`, `#`, `mailto:` y `tel:`, sin caracteres de control); `.git/`, `.github/`
y el propio `mas.cms.json` no se pueden modificar; las imágenes se comprueban por su contenido (PNG, JPEG, GIF,
WebP, AVIF; nada de SVG) y reciben un nombre de archivo nuevo. Cada guardado es un único commit (`cms(home): … Edited
by <editor>`) sobre la versión que abrió el editor: si alguien la cambió mientras tanto, se le pide al editor
que recargue en lugar de sobrescribir. Deshaz un cambio con `git revert` o vuelve atrás el
despliegue en Mas. Las colecciones de Supabase se escriben con la clave service_role, que nunca sale de
Mas; el sitio las lee con su clave anon (row level security: solo lectura).

### Balanceo de carga

La tarjeta **Balanceo de carga** de una app Node.js, Next.js o Python reparte su tráfico:

- **Instancias en este servidor**: Mas ejecuta la app N veces (`mas-app-<id>.service`,
  `mas-app-<id>.2.service`…, cada una en su propio puerto de loopback) para aprovechar todos los núcleos de la CPU. Un nuevo despliegue
  las reinicia de una en una y comprueba cada una antes de pasar a la siguiente, así que el sitio nunca se cae;
  si una instancia no vuelve, se da marcha atrás a la release.
- **Servidores del clúster**: copias compiladas por Mas en los agentes que marques (ver
  [Varios servidores](#varios-servidores-clúster)).
- **Otros servidores**: `host:port` de copias de la app que ejecutas en otro lugar, normalmente a través de una
  red privada. Se añaden al mismo grupo.

Con más de un backend, el vhost recibe un `upstream` de nginx (`least_conn`): un backend que
falla dos veces se omite durante 10 s, y una petición que llega a un backend reiniciándose o caído se reintenta
en el siguiente. Repartir las peticiones implica que la app no debe guardar estado en memoria ni en el disco local
(sesiones, subidas, cachés): usa la base de datos o el almacenamiento de Supabase. Los sitios estáticos y PHP
los sirve nginx directamente y no necesitan nada de esto.

### Mientras se despliega un sitio

En un primer despliegue, el dominio responde de inmediato con una **página “Desplegando…”** (vhost provisional
de nginx) y obtiene su certificado **antes** de la compilación, para que los visitantes nunca acaben en el
vhost de otro sitio (el `421 Misdirected Request` que obtendrías si no por HTTP/2). El sitio real la sustituye
cuando pasa la comprobación de salud; si el primer despliegue falla, se queda una página de “aún no disponible”.
Las apps tras proxy también muestran la página durante el segundo o dos de un reinicio en lugar de un 502.

### Sustituir un sitio detectado por una app

Un sitio que Mas detectó (una carpeta PHP, un sitio estático, una app PM2…) se puede sustituir por una app de Mas compilada desde
su repositorio, **sin interrupción**: la app se compila y se comprueba junto al sitio antiguo, y después
los archivos de nginx antiguos se van y llega el vhost de la app en la misma recarga. Si el primer despliegue
falla, el sitio antiguo se queda como estaba. La página del servicio ofrece **Sustituir por una app de Mas**; eliminar
la app más adelante restaura el sitio antiguo.

### Volver a desplegar todo

**Apps → Volver a desplegar todo** pone en cola un despliegue del último commit de cada app desde GitHub; se ejecutan
uno tras otro (los servicios del catálogo no se tocan).

### Adoptar un servicio existente

Un servicio detectado cuya carpeta es un checkout de GitHub (por ejemplo, una app PM2 o systemd) muestra
**Conectar con GitHub** en su página. Mas lo despliega entonces **donde está**, sin reinstalar nada:
`git fetch` + `git reset --hard` en su carpeta (como el propietario de la carpeta, se rechaza si hay cambios locales
sin commit), compilación, reinicio mediante su gestor actual, comprobación de salud, y
`git reset` al commit anterior si falla.

### CLI

```console
$ sudo mas apps                    # apps, live release, last deploy
$ sudo mas deploy <app>            # deploy now, logs printed live
$ sudo mas releases <app>
$ sudo mas rollback <app> [release]
$ sudo mas github status
```

Los despliegues de vista previa por pull request aún no están implementados; el modelo de datos (un `environment`
por app) ya está preparado para ellos.

## Administrar el servidor

### Recursos

La página **Recursos** muestra qué usa la memoria y la CPU del servidor, todo a la vez,
actualizado cada 10 segundos: una fila por app de Mas (todas sus instancias), por proyecto compose (servicios
del catálogo, Supabase, tus propios archivos compose), por cada otro servicio del sistema, el propio Mas y las sesiones
de usuario, ordenadas por memoria, con la CPU de cada una. Lee los cgroups de systemd
(`memory.current` sin la caché de archivos inactiva, `cpu.stat`), así que medir todo el servidor
no cuesta casi nada.

Cada app Node.js, Next.js o Python tiene una tarjeta **Recursos** (también editable desde esa página):

- **Límite de memoria**: se escribe en sus unidades (`MemoryHigh` al 90 %, `MemoryMax`); las apps Node reciben un
  `--max-old-space-size` acorde. Una app con fugas se ralentiza y se reinicia sola en lugar de quitarles
  la memoria a las demás.
- **Reposo en inactividad**: tras N minutos sin peticiones, la app se detiene y no consume nada. El
  siguiente visitante la despierta a través de Mas y mientras tanto ve una breve página de "arrancando".
- **Las instancias siguen el tráfico**: entre un mínimo y un máximo, una instancia más por cada 600
  peticiones por minuto o cuando la CPU se mantiene por encima del 70 %, una menos tras 10 minutos tranquilos. Las instancias se
  inician y se detienen sin recompilar.
- **Consejos** a partir de lo que la app usa realmente: un límite que conviene fijar, un límite demasiado ajustado, una app que
  podría dormir, instancias que podrían seguir el tráfico, `output: "standalone"` para Next.js, un
  contenedor de Minecraft que reserva toda su memoria. **Aplicar** aplica uno, **Aplicar todos los consejos**
  los aplica todos.

### Estadísticas de tráfico

La página de cada app y de cada sitio del servidor tiene una tarjeta **Tráfico**: peticiones, páginas vistas y
visitantes por día (de 7 a 365 días) y las páginas más vistas. Se cuentan a partir de los registros de acceso
de nginx, de forma incremental, sin cookies ni scripts: no se guarda ninguna dirección IP, los robots, las imágenes y los scripts
quedan fuera de las páginas vistas, y un visitante se cuenta una vez al día. Los sitios sin registro de acceso
propio se cuentan a partir de `/var/log/nginx/mas-hosts.access.log`, que Mas añade para ellos (cada línea
empieza por el nombre del sitio).

### Modo mantenimiento

**Apps → Mantenimiento** (o la tarjeta **Mantenimiento** de una app) pone un sitio en pausa: los visitantes reciben una
página que tú eliges con un `503` y `Retry-After` (los buscadores vuelven más tarde), las direcciones
que autorices siguen viendo el sitio real, y la app sigue en marcha. Un mantenimiento puede terminar solo a una
hora fijada, y todos los sitios pueden entrar en mantenimiento (o salir de él) a la vez.

- Las **Páginas** las escribe Mas a partir de un título, un mensaje, una hora de vuelta y un color, o cada una puede ser **importada**:
  un archivo HTML, o un ZIP de una carpeta con un `index.html` y sus imágenes, estilos y fuentes. Mas añade
  un `<base>` para que los enlaces relativos funcionen desde cualquier URL del sitio. Cada página tiene una vista previa y se puede
  usar en todos los sitios.
- Activarlo nunca reescribe el vhost: Mas escribe un `geo` y un `map` en
  `/etc/nginx/conf.d/mas-maintenance.conf` y un fragmento que incluye el vhost, prueba con `nginx -t`
  y recarga; la página y los desafíos ACME siempre pasan.
- Se aplica a los sitios cuya configuración de nginx escribe Mas (apps y servicios con dominio).

### Reiniciar todo, idéntico

**Resumen → Reiniciar todo** reinicia cada servicio en marcha exactamente como estaba, tras mostrar
la lista (puedes desmarcar algunos). Los contenedores se reinician en su sitio mediante la API de Docker, nunca se
recrean: mismo contenedor, imagen, variables, volúmenes y puertos. Las unidades se reinician con
`systemctl restart`, las apps PM2 con `pm2 restart`, después las apps de Mas instancia a instancia con una
comprobación de salud, y nginx se recarga al final tras `nginx -t`. Dentro de un proyecto compose, los contenedores
de los que dependen los demás van primero. Mas compara la configuración de cada servicio antes y después e
indica *idéntico* o lo que ha cambiado; lo que estaba detenido sigue detenido. El propio Mas, nginx, Docker y
SSH no se tocan.

### Comprobaciones de salud

`mas doctor` (CLI) y la página **Salud** comprueban, en solo lectura:

| Comprobación | Por qué importa |
|-------|----------------|
| Linaje de certbot roto (`live/*/cert.pem` no es un enlace simbólico), configuración de renovación sin archivos | certbot lo omite o falla en cada ejecución |
| Certificados caducados / a punto de caducar | la renovación está fallando en algún sitio |
| Certificados que no usa ningún vhost activo | restos que hacen fallar `certbot renew` |
| Renovaciones `standalone` con hooks `systemctl stop nginx` | cada renovación tumba todos los sitios, y puede dejar nginx detenido |
| Webroot inexistente, `certbot.timer` inactivo | las renovaciones no pueden salir bien |
| nginx ejecutándose fuera de systemd | `systemctl reload` no hace nada, los reinicios fallan con "address already in use" |
| vhost que apunta a un certificado inexistente, `nginx -t` fallando | la próxima recarga rompe todos los sitios |
| Dominios que no resuelven, o que resuelven a otro servidor | sitio trasladado a otro lugar: su vhost/certificado aquí es peso muerto |

Cada problema viene con una explicación y, cuando es seguro, el comando que lo soluciona. Mas nunca
aplica estas soluciones por sí mismo.

## Varios servidores (clúster)

La página **Servidores** convierte varios servidores Mas en un único clúster, controlado desde un solo panel.

1. En cada uno de los otros servidores, instala Mas y conviértelo en un **agente**: sin panel, solo responde a las
   llamadas del servidor principal en su dirección privada (una red VPC, WireGuard o Tailscale, nunca
   una pública).

   ```console
   $ sudo mas agent init --address 10.0.0.2      # prints the join token, shown once
   $ sudo mas agent token                        # another token later
   ```

2. En el principal: **Servidores → Añadir un servidor** con `http://10.0.0.2:7070` y el token. Los tokens se
   guardan como hash, dan derecho a desplegar código en ese servidor y se pueden revocar.
3. En la tarjeta **Balanceo de carga** de una app, marca los servidores en los que debe ejecutarse. Cada despliegue compila el
   mismo commit en cada uno de ellos (con un token de clonado de corta duración de la GitHub App del principal), y
   nginx en el principal reparte las peticiones entre todas las copias que responden.

- **Mapa en vivo**: qué servidor ejecuta qué copia, y las peticiones del último minuto por backend.
- **Relleno automático**: un servidor que lleva 2 minutos en silencio ve sus copias recompiladas en otro servidor disponible.
- **Si se pierde el principal**: el principal envía continuamente su estado (apps, ajustes, cifrado) a los
  agentes. En uno de ellos, `sudo mas agent promote` lo convierte en el principal: sus copias pasan a ser las apps,
  las demás apps se compilan allí, y controla los servidores restantes. Apunta los dominios a él; si
  el antiguo principal vuelve, los demás rechazan sus órdenes (cada promoción incrementa una época).
- `sudo mas agent leave` convierte un agente de nuevo en un Mas normal con su panel.

## Cómo funciona la detección

```
nginx vhost ──proxy_pass──▶ port ──ss──▶ PID ──/proc──▶ cgroup → manager (systemd / pm2 / docker)
                                                 └────▶ cwd    → project folder → type, git
nginx vhost ──fastcgi_pass + root──▶ PHP-FPM site
nginx vhost ──root only──▶ static site
listening port / pm2 app / container / unit without vhost ──▶ "no domain" service
```

| Recolector      | Fuente                                                                 |
|-----------------|------------------------------------------------------------------------|
| `nginx`         | `/etc/nginx/sites-enabled/*`, `conf.d/*.conf`, includes, upstreams, certificados |
| `ports`         | `ss -tlnpH` (TCP) y `ss -xlnpH` (sockets unix)                         |
| `processes`     | `/proc/<pid>/{status,cmdline,cwd,cgroup,environ}`                      |
| `systemd`       | `systemctl list-units` / `systemctl show`, con las unidades del SO filtradas |
| `pm2`           | `pm2 jlist` para cada usuario con un `~/.pm2` (solo si su daemon está en marcha) |
| `docker`        | `/var/run/docker.sock` (se omite limpiamente si no existe)             |

Los bloques de redirección "80 → 443" de certbot y las redirecciones `www.` se fusionan con el bloque HTTPS del
mismo sitio. Los metadatos de git (remoto, rama, último commit) se leen directamente de `.git/`, quitando
las credenciales incrustadas en la URL del remoto.

Los tipos de servicio se deciden mediante reglas ordenadas: `fastcgi_pass` o archivos `.php` → **php**,
`package.json` con `next` → **nextjs**, `package.json` → **node**,
`requirements.txt` / `pyproject.toml` → **python**, `root` sin proxy → **static**,
si no **desconocido**. Una etiqueta puesta a mano en el panel siempre tiene prioridad.

### Configuración

`/etc/mas/config.toml`:

```toml
[server]
port = 7070           # always bound to 127.0.0.1
session_hours = 12    # idle timeout (sessions also expire after 7 days)
scan_interval = 60    # background scan, seconds

[scan]
extra_exclude_units = ["my-backup-agent"]  # added to the built-in OS list
hide_os_daemons = true  # hide units with no port and only system paths in their command
```

## Modelo de seguridad

- **Red**: la API solo escucha en `127.0.0.1:7070` (`mas serve` rechaza cualquier otra cosa).
  El punto de entrada público es el vhost de nginx: solo HTTPS, HSTS, `X-Frame-Options: DENY`, CSP,
  `client_max_body_size 2m`.
- **Autenticación**: las cuentas de administrador se crean en el servidor (`mas user create`), nunca
  desde la web. Las cuentas de editor del CMS las crea un administrador en el panel y solo pueden
  acceder a las rutas del CMS de los sitios que se les asignaron. Las contraseñas se cifran con hash argon2id. **2FA TOTP** (RFC 6238, 30 s, ±1 paso), y
  cada código se acepta una sola vez (protección contra repetición). Cinco inicios de sesión fallidos por cuenta o diez por
  IP en 15 minutos provocan un bloqueo temporal (HTTP 429). Los inicios de sesión se anotan en el registro de auditoría.
- **Sesiones**: token aleatorio de 256 bits en una cookie `HttpOnly`, `Secure`, `SameSite=Strict`; solo
  se guarda su SHA-256. 12 h de inactividad como máximo, 7 días en total. Cambiar la contraseña o el dispositivo
  de 2FA cierra todas las demás sesiones.
- **CSRF**: cada petición que modifica algo necesita la cabecera `X-CSRF-Token` de la sesión, y se rechaza un
  `Origin` ajeno.
- **Despliegues**: el webhook de GitHub se rechaza salvo que su HMAC `X-Hub-Signature-256` sea válido,
  incluso antes de analizar el contenido. Las apps se ejecutan con su propio usuario no root, escuchan en loopback y
  reciben sus secretos de un archivo de entorno `0600`; los secretos se guardan cifrados en la base de datos.
  Los comandos de compilación que configuras se ejecutan con ese usuario sin privilegios. Los archivos generados se respaldan
  antes de sustituirse y nginx solo se recarga tras un `nginx -t` correcto.
- **Refuerzo tras la revisión de seguridad**: los comandos de compilación/arranque son de una sola línea (sin inyección de
  directivas systemd), la carpeta servida no puede salir de la release (`..` rechazado), las rutas y refs de GitHub
  se validan y se codifican para URL, cada entrega de webhook se acepta una sola vez, sustituir el dispositivo de 2FA
  exige un código actual, las comprobaciones de contraseña dentro de una sesión tienen límite de frecuencia, la cookie de sesión
  lleva el prefijo `__Host-`, se envían las cabeceras `Permissions-Policy` y `Cross-Origin-Opener-Policy`,
  y las unidades de las apps se ejecutan sin capabilities (`CapabilityBoundingSet=`), `PrivateDevices`,
  `ProtectKernel*`, `RestrictSUIDSGID`.
- **Acciones**: lista de permitidos estricta `(manager, action) → argv`, construida a partir de identificadores encontrados por
  el escaneo (revalidados con expresiones regulares), ejecutada sin shell. Detener y reiniciar piden
  confirmación. Cada acción, con su comando y su salida, se anota en el registro de auditoría.
- **nginx**: Mas solo recarga tras un `nginx -t` correcto. El instalador respalda cualquier archivo que
  sustituye (`<file>.mas-bak-<timestamp>`) y lo restaura si `nginx -t` falla.
- **Descubrimiento de solo lectura**: los escaneos nunca escriben. La batería de tests calcula el hash de todo el árbol de fixtures
  antes y después de un escaneo y pone en lista de permitidos cada comando ejecutado. `pm2 jlist` solo se llama
  cuando el daemon del usuario ya está en marcha (de lo contrario iniciaría uno), y con ese usuario.
- **Secretos** encontrados por el camino (credenciales en remotos git, variables de entorno de PM2): no se
  guardan ni se muestran. La base de datos (`/var/lib/mas/mas.db`) es `0600`, solo root.
- **Clúster**: los tokens de unión son aleatorios, se guardan como hash y se pueden revocar; los servidores solo hablan por la red
  privada; el estado enviado a los agentes va cifrado, y la época de un servidor promovido hace que los
  demás rechacen a un antiguo principal.
- **Páginas de mantenimiento**: son archivos públicos (todo visitante de un sitio en mantenimiento los recibe): el
  panel las previsualiza en un marco aislado sin scripts; los ZIP importados no pueden escribir fuera de
  su carpeta ni añadir archivos ocultos.
- **Teléfono perdido**: `sudo mas user 2fa-reset` en el servidor. Root en el servidor es la
  vía de recuperación.
- **Por qué el agente se ejecuta como root**: debe leer `/proc` de todos los usuarios, `ss -p` y el socket de
  Docker, y reiniciar servicios. `mas.service` reduce el radio de impacto con
  `NoNewPrivileges=yes`, `ProtectSystem=full`, `PrivateTmp=yes`, `ProtectKernel*`, y
  `ReadWritePaths=` limitado a `/var/lib/mas /srv/mas /etc/nginx /etc/mas /etc/systemd/system
  /etc/letsencrypt`.

## Beta: limitaciones conocidas

- Solo Debian (y derivadas) con nginx; los servidores Apache/Caddy no se detectan.
- Las cuentas de administrador se crean en el servidor; aún no hay códigos de recuperación de 2FA (`sudo mas user 2fa-reset`).
- Aún no hay despliegues de vista previa por pull request; una rama de producción por app.
- Los proveedores DNS distintos de OVH y Cloudflare están implementados a partir de la documentación de su API y
  aún no se han probado a fondo: informa de cualquier problema.
- La CSP del panel mantiene `'unsafe-inline'` (necesario para la exportación estática de Next.js).
- Los recursos y el tráfico necesitan cgroup v2 (Debian 11+): en sistemas más antiguos solo se miden las apps de Mas.

## Desarrollo

```console
$ python -m venv .venv && .venv/bin/pip install -e ".[dev]"
$ .venv/bin/pytest
$ cd web && npm ci && npm run build      # dashboard → web/out
```

Los tests no necesitan un servidor real. `tests/fixtures/server.py` construye un árbol Debian falso (configuraciones
de nginx, `/proc`, salidas de `ss` y `systemctl`, un jlist de PM2, respuestas de la API de Docker), y
`FixtureHost` se lo sirve a los recolectores. Puedes ejecutar la app entera sobre él:

```console
$ python tests/fixtures/server.py /tmp/mas-demo
$ mas --fixture /tmp/mas-demo --db /tmp/mas-demo.db user create admin
$ mas --fixture /tmp/mas-demo --db /tmp/mas-demo.db serve --insecure-cookie
```

## Hoja de ruta

- [x] **Fase 1: detección.** Recolectores, correlación, detección de tipo, `mas scan [--json]`.
- [x] **Fase 2: panel y gestión.** API, 2FA, escaneo en segundo plano, registros, acciones en lista
      de permitidos, etiquetas, registro de auditoría, `install.sh`, `mas.service`, `mas self-update`.
- [x] **Fase 2.5: comprobaciones de salud.** Certificados, renovación de certbot, nginx, DNS (`mas doctor`).
- [x] **Fase 3: despliegue desde GitHub.** GitHub App (flujo de manifiesto), webhooks firmados, releases
      atómicas, unidad systemd y vhost de nginx generados, certbot, comprobación de salud con vuelta atrás
      automática, 5 releases conservadas, registros de compilación en directo (SSE), adopción de servicios detectados.
- [x] **Balanceo de carga en un servidor**: varias instancias por app, backends externos, reinicios escalonados.
- [x] **Servicios, Supabase y CMS**: servicios listos para usar con copias de seguridad, Supabase autoalojado, un CMS
      para clientes, estadísticas de tráfico, panel en cuatro idiomas.
- [x] **Clúster**: agentes, copias de apps en varios servidores, mapa en vivo, relleno automático, promoción.
- [x] **Recursos y operaciones**: página Recursos, límites de memoria, reposo en inactividad, autoescalado,
      consejos, reinicio idéntico de todo, modo mantenimiento.
- [ ] Próximamente: despliegues de vista previa por pull request, códigos de recuperación para la 2FA, notificaciones.

## Licencia

MIT
