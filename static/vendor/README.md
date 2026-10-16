# Локальный парсер Markdown

`markdown-parser.js` — собранный для браузера разборщик Markdown в дерево с
исходными позициями. Используются:

- [mdast-util-from-markdown](https://github.com/syntax-tree/mdast-util-from-markdown), 2.0.3;
- [mdast-util-gfm](https://github.com/syntax-tree/mdast-util-gfm), 3.1.0;
- [micromark-extension-gfm](https://github.com/micromark/micromark-extension-gfm), 3.0.0.
- [Turndown](https://github.com/mixmark-io/turndown), 7.2.4, и
  [turndown-plugin-gfm](https://github.com/mixmark-io/turndown-plugin-gfm), 1.0.2,
  для преобразования изменённых блоков визуального редактора в Markdown.

Все лицензии включённых пакетов — `markdown-parser.LICENSE.txt`.
Версии и целостность зависимостей закреплены в `tools/markdown-build/package-lock.json`.
Сборка: из `tools/markdown-build` выполнить `npm ci --ignore-scripts`, затем
`npm run build`. Для запуска Гранки Node и npm не требуются.

Гранка строит DOM сама, разрешая только предусмотренные типы узлов. Исходный
HTML не вставляется в DOM, скрипты из статьи не исполняются. Сборка не загружает
библиотеки из CDN и не отправляет текст статьи в сеть.

Вставка картинки использует только HTML, построенный из доверенных DOM-узлов
Гранки; буфер обмена вставляется обычным текстом. Команды браузера сохраняют
нативную историю отмены. Неизменённые блоки статьи берутся из исходника без
повторного преобразования.
