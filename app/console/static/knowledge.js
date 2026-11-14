import {
  state,
  has,
  product,
  request,
  post,
  patch,
  remove,
  directory,
  main,
  active,
  go,
  on,
  notify,
  confirmAction,
  formDialog,
  bindPager,
  bindSearch,
  searchBar,
  back,
  refresh,
  permission,
  showInfo,
  awaitRun,
} from "./app.js";
import {
  el,
  all,
  heading,
  esc,
  btn,
  panel,
  empty,
  badge,
  table,
  pager,
  link,
  field,
  control,
  area,
  opt,
  note,
  idAttr,
} from "./ui.js";
const path = (id) => "/knowledge-bases/" + encodeURIComponent(id);
const knowledgeTable = (headers, rows) =>
  '<div class="knowledge-table">' + table(headers, rows) + "</div>";
const managePath = (id) => "/console/manage" + path(id);

function editBook(rec, revision, done) {
  return formDialog({
    title: "编辑资料库",
    fields: field(
      "资料库名称",
      control("name", rec.name, "", 'required maxlength="200"'),
    ),
    submit: "保存名称",
    work: async (f) => {
      await patch(managePath(rec.id), {
        name: f.get("name").trim(),
        expected_revision: revision,
      });
      notify("资料库名称已更新");
      await done();
    },
  });
}

function deleteBook(rec, done) {
  return confirmAction(
    "删除资料库",
    "删除“" +
      rec.name +
      "”及库内所有文件。引用这些文件的助手将无法再使用其内容。",
    async () => {
      await remove(managePath(rec.id));
      notify("资料库已删除");
      await done();
    },
  );
}

export async function knowledge(n, parts) {
  if (!has("config", "publish", "business")) {
    main().innerHTML = permission();
    return;
  }
  if (parts[1] === "view") return book(n, parts[2]);
  let page = 1,
    q = "",
    ticket = 0;
  async function draw() {
    const step = ++ticket,
      data = await directory("books", { search: q, page });
    if (!active(n) || ticket !== step) return;
    const rows = data.items.map((k) => [
      link(k.name, "knowledge/view/" + k.id),
      esc(state.ws.tenant),
      esc(product(k.product_id)),
      k.version ? "已发布 · 第 " + k.version + " 版" : "尚未发布",
      '<div class="actions">' +
        link(
          state.ws.manage_knowledge ? "管理文件" : "查看资料",
          "knowledge/view/" + k.id,
        ) +
        (state.ws.manage_knowledge
          ? btn(
              "编辑资料库",
              "edit-book",
              "修改资料库名称",
              "linkbtn",
              idAttr(k.id),
            ) +
            btn(
              "删除资料库",
              "delete-book",
              "删除资料库及库内所有文件",
              "linkbtn danger-zone",
              idAttr(k.id),
            )
          : "") +
        "</div>",
    ]);
    main().innerHTML =
      heading(
        "企业与产品资料库",
        state.ws.manage_knowledge
          ? "管理员可以新建、编辑和删除资料库。进入“管理文件”可上传、编辑或删除库内文件。"
          : "资料只读。你可以查看已发布的文件，并在自己的助手中引用。",
        state.ws.manage_knowledge
          ? btn("新建资料库", "new", "为指定产品创建一处资料目录", "primary")
          : "",
      ) +
      panel(
        "资料库目录",
        searchBar(q, "搜索资料库名称") +
          (rows.length
            ? knowledgeTable(
                ["资料库名称", "所属企业", "对应产品", "当前版本", "操作"],
                rows,
              )
            : empty(
                "暂无符合条件的资料库",
                state.ws.manage_knowledge
                  ? "可先建立资料库，然后上传文本或 Markdown 文件。"
                  : "管理员发布产品资料后，即可在这里查看。",
              )) +
          pager(data),
      );
    on(main(), "edit-book", async (b) => {
      const manifest = await request(path(b.dataset.id));
      return editBook(
        { id: manifest.kb_id, name: manifest.name },
        manifest.edit_revision,
        draw,
      );
    });
    on(main(), "delete-book", (b) =>
      deleteBook(
        data.items.find((k) => k.id === b.dataset.id),
        () => {
          if (data.items.length === 1 && page > 1) page--;
          return draw();
        },
      ),
    );
    bindPager(main(), data, (p) => {
      page = p;
      return draw();
    });
    bindSearch(main(), (v) => {
      q = v;
      page = 1;
      return draw();
    });
    on(main(), "new", () =>
      formDialog({
        title: "新建资料库",
        fields:
          field(
            "资料库名称",
            control(
              "name",
              "",
              "例如：排班产品说明",
              'required maxlength="200"',
            ),
            "选择和产品相关的名称。",
          ) +
          field(
            "对应产品",
            '<select name="product" required>' +
              state.ws.products.map((p) => opt(p.id, p.name)).join("") +
              "</select>",
            "仅供当前企业、这款产品的助手使用。",
          ),
        submit: "创建资料库",
        work: async (f) => {
          const r = await post("/knowledge-bases", {
            name: f.get("name").trim(),
            product_id: f.get("product"),
          });
          notify("资料库已创建，可以上传文件");
          go("knowledge/view/" + r.kb_id);
        },
      }),
    );
  }
  await draw();
}
async function book(n, id) {
  const [dir, manifest] = await Promise.all([
    directory("books", { id }),
    request(path(id)),
  ]);
  const manage = managePath(id);
  const rec = dir.items[0];
  if (!rec) throw new Error("未找到资料库。");
  const published = rec.version
    ? (await request(path(id), { query: { snapshot: rec.version } })).documents
    : [];
  const live = new Set(
    published.filter((d) => d.status === "ready").map((d) => d.document_id),
  );
  if (!active(n)) return;
  function docState(d) {
    return live.has(d.document_id) && d.status === "ready"
      ? "published"
      : d.status;
  }
  const currentIds = new Set(manifest.documents.map((d) => d.document_id));
  const previous = published.filter(
    (d) => live.has(d.document_id) && !currentIds.has(d.document_id),
  );
  const approvedByFile = new Map(previous.map((d) => [d.file_id, d]));
  const publishable = manifest.documents.filter(
    (d) => d.status === "ready" || approvedByFile.has(d.file_id),
  );
  function approvalField(d) {
    const old = approvedByFile.get(d.file_id);
    if (old) {
      return field(
        d.title,
        '<select name="documents">' +
          opt(
            old.document_id,
            "保留已批准的第 " + old.revision + " 次修订",
            old.document_id,
          ) +
          (d.status === "ready"
            ? opt(d.document_id, "批准新的第 " + d.revision + " 次修订")
            : "") +
          opt("", "从下一版移除") +
          "</select>",
        "默认保留旧原文。核对新原文后再批准替换。",
      );
    }
    return (
      '<label class="check"><input type="checkbox" name="documents" value="' +
      esc(d.document_id) +
      '" ' +
      (live.has(d.document_id) ? "checked" : "") +
      "><span>" +
      esc(d.title) +
      "<small>" +
      (live.has(d.document_id)
        ? "已批准，默认保留。"
        : "勾选表示批准加入下一版。") +
      "</small></span></label>"
    );
  }
  const rows = manifest.documents.map((d) => [
    esc(d.title),
    badge("doc", docState(d)),
    d.status === "processing"
      ? "尚未审核"
      : live.has(d.document_id)
        ? "第 " + rec.version + " 版已发布"
        : d.status === "ready"
          ? "未纳入当前发布版"
          : "不可引用",
    '<div class="actions">' +
      btn(
        "查看原文",
        "preview",
        "核对完整文件内容与来源",
        "linkbtn",
        idAttr(d.document_id),
      ) +
      (state.ws.manage_knowledge
        ? btn(
            "编辑文件",
            "edit-doc",
            "编辑标题、原文和来源，生成待审核修订",
            "linkbtn",
            idAttr(d.document_id),
          )
        : "") +
      (state.ws.manage_knowledge
        ? btn(
            "删除文件",
            "delete-doc",
            "从资料库移除此文件的全部修订",
            "linkbtn danger-zone",
            idAttr(d.document_id),
          )
        : "") +
      (state.ws.manage_knowledge && d.status !== "withdrawn"
        ? btn(
            "撤回",
            "withdraw",
            "禁止所有助手继续引用此文件修订",
            "linkbtn danger-zone",
            idAttr(d.document_id),
          )
        : "") +
      "</div>",
  ]);
  main().innerHTML =
    back("knowledge", "返回资料库目录") +
    heading(
      rec.name,
      state.ws.tenant +
        " · " +
        product(rec.product_id) +
        " · " +
        (rec.version ? "当前第 " + rec.version + " 版" : "尚未发布"),
      btn("刷新状态", "refresh", "重新读取文件解析和审核状态") +
        (state.ws.manage_knowledge
          ? btn("编辑资料库", "edit-book", "修改资料库名称")
          : "") +
        (state.ws.manage_knowledge
          ? btn(
              "删除资料库",
              "delete-book",
              "移除资料库和其中全部文件",
              "danger",
            )
          : "") +
        (state.ws.manage_knowledge
          ? btn(
              "上传资料",
              "upload",
              "上传 UTF-8 文本或 Markdown 文件",
              "primary",
            )
          : "") +
        (state.ws.manage_knowledge && publishable.length
          ? btn("批准并发布", "approve", "核对纳入下一版的全部文件并发布版本")
          : ""),
    ) +
    panel(
      "资料文件",
      note(
        "上传后先解析文件。只有经审核并纳入已发布版本的内容，才可被助手引用。",
      ) +
        (rows.length
          ? knowledgeTable(
              ["文件名称", "审核状态", "版本使用情况", "操作"],
              rows,
            )
          : empty(
              state.ws.manage_knowledge
                ? "这里还没有文件"
                : "当前没有可查看的已发布文件",
              state.ws.manage_knowledge
                ? "支持 .txt 或 .md 格式的 UTF-8 文本，每个文件最多 1 MiB。"
                : "管理员批准并发布可用文件后，即可查看。",
            )) +
        (previous.length
          ? "<details><summary>仍在当前发布版中的旧原文</summary>" +
            knowledgeTable(
              ["文件名称", "文件修订", "操作"],
              previous.map((d) => [
                esc(d.title),
                String(d.revision),
                '<div class="actions">' +
                  btn(
                    "查阅已发布原文",
                    "preview",
                    "查看当前发布版引用的旧修订",
                    "linkbtn",
                    idAttr(d.document_id),
                  ) +
                  (state.ws.manage_knowledge
                    ? btn(
                        "撤回该修订",
                        "withdraw",
                        "立即停止引用这份旧原文",
                        "linkbtn danger-zone",
                        idAttr(d.document_id),
                      )
                    : "") +
                  "</div>",
              ]),
            ) +
            "</details>"
          : ""),
    );
  on(main(), "edit-book", () => editBook(rec, manifest.edit_revision, refresh));
  on(main(), "delete-book", () => deleteBook(rec, () => go("knowledge")));
  const docPath = (doc) => manage + "/documents/" + encodeURIComponent(doc);
  on(main(), "edit-doc", async (b) => {
    const doc = b.dataset.id;
    const original = await request(
      "/console/data/documents/" + encodeURIComponent(doc),
    );
    await formDialog({
      title: "编辑文件",
      description:
        "保存后生成新的待审核修订。已发布助手仍使用原先批准的版本。若旧内容有误，可以删除整份文件来停止引用。",
      fields:
        field(
          "文件名称",
          control("title", original.title, "", 'required maxlength="200"'),
        ) +
        field(
          "文件原文",
          area(
            "text",
            original.text,
            "请输入文件内容",
            'required maxlength="1048576"',
          ),
        ) +
        field(
          "资料来源（选填）",
          control("source_note", original.source || "", "", 'maxlength="1000"'),
        ),
      submit: "保存新修订",
      work: async (f) => {
        const r = await patch(docPath(doc), {
          expected_revision: original.revision,
          title: f.get("title"),
          text: f.get("text"),
          source_note: f.get("source_note") || null,
        });
        const run = await awaitRun(r.run_id, 4000);
        notify(
          run.status === "succeeded"
            ? "新修订已就绪，请审核并发布"
            : run.status === "failed"
              ? "新修订已保存，解析失败，请检查原文"
              : "新修订已保存，可刷新查看解析状态",
        );
        refresh();
      },
    });
  });
  on(main(), "delete-doc", (b) => {
    const d = manifest.documents.find((v) => v.document_id === b.dataset.id);
    return confirmAction(
      "删除文件",
      "删除“" +
        d.title +
        "”的全部修订。引用这些修订的助手将无法继续使用该文件。",
      async () => {
        await remove(docPath(d.document_id));
        notify("文件已删除");
        refresh();
      },
    );
  });
  on(main(), "refresh", refresh);
  on(main(), "preview", async (b) => {
    const d = await request(
      "/console/data/documents/" + encodeURIComponent(b.dataset.id),
    );
    showInfo(
      d.title,
      "<p>文件修订 " +
        d.revision +
        " · 来源：" +
        esc(d.source || "上传时未注明") +
        '</p><pre class="doc-preview">' +
        esc(d.text) +
        "</pre>",
    );
  });
  on(main(), "upload", () =>
    formDialog({
      title: "上传资料",
      fields:
        field(
          "文件名称",
          control(
            "title",
            "",
            "例如：产品服务范围",
            'required maxlength="200"',
          ),
          "用于识别审核对象。",
        ) +
        field(
          "选择本地文件",
          '<input type="file" name="file" accept=".txt,.md" required>',
          "支持 UTF-8 文本和 Markdown，每个文件最多 1 MiB。",
        ) +
        field(
          "资料来源（选填）",
          control(
            "source_note",
            "",
            "例如：吴姐确认的产品说明",
            'maxlength="1000"',
          ),
          "便于日后核对出处。",
        ),
      submit: "上传并解析",
      work: async (f) => {
        const file = f.get("file");
        if (!/\.(txt|md)$/i.test(file.name) || file.size > 1048576)
          throw new Error("请选择不超过 1 MiB 的 .txt 或 .md 文件。");
        const r = await post(path(id) + "/documents", f);
        notify("文件已提交解析");
        const out = await awaitRun(r.run_id, 4000);
        notify(
          out.status === "succeeded"
            ? "文件解析完成，等待审核发布"
            : out.status === "failed"
              ? "文件解析失败，请核对格式与内容"
              : out.status === "unverified"
                ? "文件已上传，解析结果待查询。请刷新状态。"
                : "文件正在等待解析，请刷新状态查看",
        );
        refresh();
      },
    }),
  );
  on(main(), "approve", () =>
    formDialog({
      title: "批准并发布资料",
      description:
        "选择要加入下一版的文件。已有新修订的文件默认沿用已批准原文。发布后，旧任务继续使用原先绑定的版本。",
      fields: publishable.map(approvalField).join(""),
      submit: "确认批准并发布",
      work: async (f) => {
        const ids = new Set(f.getAll("documents").filter(Boolean));
        if (!ids.size) throw new Error("请至少选择一份已解析文件。");
        const r = await post(path(id) + "/publish", {
          expected_snapshot: rec.version || null,
          documents: [...manifest.documents, ...previous]
            .filter((d) => ids.has(d.document_id))
            .map(({ document_id, revision }) => ({ document_id, revision })),
        });
        notify("资料已批准并发布为第 " + r.snapshot + " 版");
        refresh();
      },
    }),
  );
  on(main(), "withdraw", (b) => {
    const d = [...manifest.documents, ...published].find(
      (v) => v.document_id === b.dataset.id,
    );
    return confirmAction(
      "撤回资料",
      "撤回“" +
        d.title +
        "”的第 " +
        d.revision +
        " 次修订后，助手将停止引用这份原文，依赖它的待发消息会被取消。",
      async (f) => {
        await post(path(id) + "/withdraw", {
          document_id: d.document_id,
          revision: d.revision,
          reason: f.get("reason"),
        });
        notify("文件已撤回");
        refresh();
      },
      true,
    );
  });
}
