"""
IR Monitor - Backend API
Escritório de contabilidade - Consulta de status IRPF
"""
from flask import Flask, jsonify, request
from flask_cors import CORS
import sqlite3, os, json
from datetime import datetime

app = Flask(__name__)
CORS(app)

DB_PATH = os.path.join(os.path.dirname(__file__), "ir_monitor.db")

# ─── Banco de dados ───────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS clientes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nome        TEXT NOT NULL,
            cpf         TEXT NOT NULL UNIQUE,
            senha_gov   TEXT,           -- armazenada criptografada em produção
            ano         INTEGER DEFAULT 2024,
            criado_em   TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS declaracoes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id      INTEGER NOT NULL,
            ano_exercicio   INTEGER NOT NULL,
            status          TEXT DEFAULT 'Transmitida',
            detalhe         TEXT,
            lote            INTEGER,
            valor_restit    TEXT,
            quotas          INTEGER,
            valor_quota     TEXT,
            valor_total_ir  TEXT,
            ultima_consulta TEXT,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id)
        );

        CREATE TABLE IF NOT EXISTS historico (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            decl_id     INTEGER NOT NULL,
            status      TEXT NOT NULL,
            detalhe     TEXT,
            data        TEXT DEFAULT (datetime('now','localtime')),
            origem      TEXT DEFAULT 'manual',   -- 'manual' ou 'automatico'
            FOREIGN KEY (decl_id) REFERENCES declaracoes(id)
        );

        CREATE TABLE IF NOT EXISTS execucoes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            inicio      TEXT,
            fim         TEXT,
            total       INTEGER DEFAULT 0,
            sucesso     INTEGER DEFAULT 0,
            erro        INTEGER DEFAULT 0,
            log         TEXT
        );

        CREATE TABLE IF NOT EXISTS anotacoes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id  INTEGER NOT NULL,
            texto       TEXT NOT NULL,
            autor       TEXT DEFAULT 'Escritório',
            criado_em   TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (cliente_id) REFERENCES clientes(id)
        );

        CREATE TABLE IF NOT EXISTS anexos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id  INTEGER NOT NULL,
            nome        TEXT NOT NULL,
            tipo        TEXT,
            dados       BLOB,
            descricao   TEXT,
            criado_em   TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (cliente_id) REFERENCES clientes(id)
        );

        CREATE TABLE IF NOT EXISTS lembretes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id  INTEGER,
            titulo      TEXT NOT NULL,
            descricao   TEXT,
            data_alerta TEXT,
            tipo        TEXT DEFAULT 'geral',
            concluido   INTEGER DEFAULT 0,
            criado_em   TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (cliente_id) REFERENCES clientes(id)
        );

        CREATE TABLE IF NOT EXISTS usuarios (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nome        TEXT NOT NULL,
            email       TEXT UNIQUE,
            senha_hash  TEXT,
            perfil      TEXT DEFAULT 'visualizador',
            ativo       INTEGER DEFAULT 1,
            criado_em   TEXT DEFAULT (datetime('now','localtime'))
        );
    """)
    conn.commit()
    conn.close()
    _migrate_db()
    _seed_demo()

def _migrate_db():
    """Adiciona colunas novas se nao existirem."""
    conn = get_db()
    novas_colunas = [
        ("declaracoes", "quotas",         "INTEGER"),
        ("declaracoes", "valor_quota",    "TEXT"),
        ("declaracoes", "valor_total_ir", "TEXT"),
        ("clientes",    "whatsapp",       "TEXT"),
        ("declaracoes", "motivo_malha",   "TEXT"),
    ]
    for tabela, col, tipo in novas_colunas:
        try:
            conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {col} {tipo}")
            conn.commit()
        except Exception:
            pass
    conn.close()

def _seed_demo():
    """Insere dados demo se banco estiver vazio."""
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]
    if count > 0:
        conn.close()
        return

    clientes_demo = [
        ("Ana Paula Ferreira", "123.456.789-01", "senha123"),
        ("Carlos Henrique Souza", "234.567.890-12", "senha456"),
        ("Mariana Costa Lima", "345.678.901-23", "senha789"),
        ("Roberto Alves Neto", "456.789.012-34", "senha321"),
        ("Fernanda Mello Santos", "567.890.123-45", "senha654"),
        ("Paulo R. Carvalho", "678.901.234-56", "senha987"),
    ]
    for nome, cpf, senha in clientes_demo:
        conn.execute("INSERT INTO clientes (nome, cpf, senha_gov) VALUES (?,?,?)", (nome, cpf, senha))
    conn.commit()

    status_demo = [
        (1, "Restituição", "Aprovada - Lote 5", 5, "R$ 2.840,00"),
        (2, "Concluída", "Sem pendências", None, None),
        (3, "Malha fina", "Renda incompatível com patrimônio declarado", None, None),
        (4, "Em processamento", "Aguardando análise da Receita", None, None),
        (5, "Concluída", "Sem pendências", None, None),
        (6, "Transmitida", "Aguardando processamento", None, None),
    ]
    for i, (cid, status, detalhe, lote, valor) in enumerate(status_demo, 1):
        conn.execute("""
            INSERT INTO declaracoes (cliente_id, ano_exercicio, status, detalhe, lote, valor_restit, ultima_consulta)
            VALUES (?,2024,?,?,?,?,datetime('now','localtime'))
        """, (cid, status, detalhe, lote, valor))
        conn.execute("INSERT INTO historico (decl_id, status, detalhe, origem) VALUES (?,?,?,?)",
                     (i, "Transmitida", "Declaração transmitida", "manual"))
        if status != "Transmitida":
            conn.execute("INSERT INTO historico (decl_id, status, detalhe, origem) VALUES (?,?,?,?)",
                         (i, status, detalhe, "automatico"))
    conn.commit()
    conn.close()

# ─── Helpers ──────────────────────────────────────────────────────────────────

def row_to_dict(row):
    return dict(row) if row else None

def rows_to_list(rows):
    return [dict(r) for r in rows]

def fmt_cpf(cpf):
    digits = "".join(filter(str.isdigit, cpf))
    if len(digits) == 11:
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
    return cpf


import os as _os

# ─── Servir Frontend ──────────────────────────────────────────────────────────
@app.route("/")
def index():
    frontend_path = _os.path.join(_os.path.dirname(__file__), '..', 'frontend', 'index.html')
    with open(frontend_path, 'r', encoding='utf-8') as f:
        return f.read()

# ─── Rotas: Clientes ──────────────────────────────────────────────────────────

@app.route("/api/clientes", methods=["GET"])
def listar_clientes():
    conn = get_db()
    rows = conn.execute("""
        SELECT c.id, c.nome, c.cpf, c.ano, c.honorario, c.forma_pgto, c.situacao_pgto, c.whatsapp,
               d.status, d.detalhe, d.lote, d.valor_restit,
               d.quotas, d.valor_quota, d.valor_total_ir, d.motivo_malha,
               d.ultima_consulta, d.id as decl_id
        FROM clientes c
        LEFT JOIN declaracoes d ON d.cliente_id = c.id AND d.ano_exercicio = c.ano
        ORDER BY c.nome
    """).fetchall()
    conn.close()
    return jsonify(rows_to_list(rows))

@app.route("/api/clientes", methods=["POST"])
def criar_cliente():
    data = request.json
    nome = data.get("nome", "").strip()
    cpf  = fmt_cpf(data.get("cpf", ""))
    senha = data.get("senha_gov", "")
    ano  = data.get("ano", 2024)

    if not nome or not cpf:
        return jsonify({"erro": "Nome e CPF são obrigatórios"}), 400

    conn = get_db()
    try:
        cur = conn.execute("INSERT INTO clientes (nome, cpf, senha_gov, ano) VALUES (?,?,?,?)",
                           (nome, cpf, senha, ano))
        cliente_id = cur.lastrowid
        conn.execute("""
            INSERT INTO declaracoes (cliente_id, ano_exercicio, status, detalhe)
            VALUES (?,?,'Transmitida','Aguardando processamento')
        """, (cliente_id, ano))
        decl_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO historico (decl_id, status, detalhe, origem) VALUES (?,'Transmitida','Cadastrado no sistema','manual')",
                     (decl_id,))
        conn.commit()
        return jsonify({"id": cliente_id, "mensagem": "Cliente criado com sucesso"}), 201
    except sqlite3.IntegrityError:
        return jsonify({"erro": "CPF já cadastrado"}), 409
    finally:
        conn.close()

@app.route("/api/clientes/<int:cid>", methods=["GET"])
def detalhe_cliente(cid):
    conn = get_db()
    cliente = row_to_dict(conn.execute("SELECT * FROM clientes WHERE id=?", (cid,)).fetchone())
    if not cliente:
        conn.close()
        return jsonify({"erro": "Cliente não encontrado"}), 404
    decl = row_to_dict(conn.execute(
        "SELECT * FROM declaracoes WHERE cliente_id=? AND ano_exercicio=?",
        (cid, cliente["ano"])).fetchone())
    historico = rows_to_list(conn.execute(
        "SELECT * FROM historico WHERE decl_id=? ORDER BY data ASC",
        (decl["id"],)).fetchall()) if decl else []
    conn.close()
    return jsonify({"cliente": cliente, "declaracao": decl, "historico": historico})

@app.route("/api/clientes/<int:cid>", methods=["PUT"])
def atualizar_cliente(cid):
    data = request.json
    conn = get_db()
    if "nome" in data or "cpf" in data or "senha_gov" in data or "whatsapp" in data:
        conn.execute("UPDATE clientes SET nome=COALESCE(?,nome), cpf=COALESCE(?,cpf), senha_gov=COALESCE(?,senha_gov), whatsapp=COALESCE(?,whatsapp) WHERE id=?",
                     (data.get("nome"), data.get("cpf"), data.get("senha_gov"), data.get("whatsapp"), cid))
    if "honorario" in data or "forma_pgto" in data or "situacao_pgto" in data:
        conn.execute("UPDATE clientes SET honorario=COALESCE(?,honorario), forma_pgto=COALESCE(?,forma_pgto), situacao_pgto=COALESCE(?,situacao_pgto) WHERE id=?",
                     (data.get("honorario"), data.get("forma_pgto"), data.get("situacao_pgto"), cid))
    if "status" in data:
        decl = row_to_dict(conn.execute(
            "SELECT d.id FROM declaracoes d JOIN clientes c ON c.id=d.cliente_id WHERE c.id=? AND d.ano_exercicio=c.ano",
            (cid,)).fetchone())
        if decl:
            conn.execute("""UPDATE declaracoes SET 
                status=?, detalhe=?, lote=?, valor_restit=?,
                quotas=?, valor_quota=?, valor_total_ir=?, motivo_malha=?,
                ultima_consulta=datetime('now','localtime') WHERE id=?""",
                (data["status"], data.get("detalhe"), data.get("lote"), data.get("valor_restit"),
                 data.get("quotas"), data.get("valor_quota"), data.get("valor_total_ir"),
                 data.get("motivo_malha"),
                 decl["id"]))
            conn.execute("INSERT INTO historico (decl_id, status, detalhe, origem) VALUES (?,?,?,?)",
                         (decl["id"], data["status"], data.get("detalhe",""), data.get("origem","manual")))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Atualizado com sucesso"})

@app.route("/api/clientes/<int:cid>", methods=["DELETE"])
def excluir_cliente(cid):
    conn = get_db()
    conn.execute("DELETE FROM clientes WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Cliente removido"})

# ─── Rotas: Estatísticas ──────────────────────────────────────────────────────

@app.route("/api/stats", methods=["GET"])
def stats():
    conn = get_db()
    rows = conn.execute("""
        SELECT d.status, COUNT(*) as total
        FROM declaracoes d
        JOIN clientes c ON c.id = d.cliente_id AND d.ano_exercicio = c.ano
        GROUP BY d.status
    """).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]
    ultima = row_to_dict(conn.execute(
        "SELECT * FROM execucoes ORDER BY id DESC LIMIT 1").fetchone())
    
    # Somar restituições com valor informado
    soma_restit = conn.execute("""
        SELECT COALESCE(SUM(CAST(REPLACE(REPLACE(REPLACE(COALESCE(d.valor_restit,'0'),'R$',''),' ',''),',','.') AS REAL)),0)
        FROM declaracoes d JOIN clientes c ON c.id=d.cliente_id AND d.ano_exercicio=c.ano
        WHERE d.status='Restituição' AND d.valor_restit IS NOT NULL AND d.valor_restit != ''
    """).fetchone()[0]
    
    # Somar IR a pagar com valor informado
    soma_ir = conn.execute("""
        SELECT COALESCE(SUM(CAST(REPLACE(REPLACE(REPLACE(COALESCE(d.valor_total_ir,'0'),'R$',''),' ',''),',','.') AS REAL)),0)
        FROM declaracoes d JOIN clientes c ON c.id=d.cliente_id AND d.ano_exercicio=c.ano
        WHERE d.status='IR a pagar' AND d.valor_total_ir IS NOT NULL AND d.valor_total_ir != ''
    """).fetchone()[0]
    
    conn.close()
    status_map = {r["status"]: r["total"] for r in rows}
    return jsonify({
        "total": total, 
        "por_status": status_map, 
        "ultima_execucao": ultima,
        "soma_restituicoes": round(soma_restit, 2),
        "soma_ir_pagar": round(soma_ir, 2),
    })

# ─── Rotas: Automação ─────────────────────────────────────────────────────────

@app.route("/api/automacao/iniciar", methods=["POST"])
def iniciar_automacao():
    """
    Dispara o script Selenium em background.
    Em produção: usar Celery ou subprocess com fila.
    """
    import subprocess, sys
    script = os.path.join(os.path.dirname(__file__), "..", "selenium", "coletor.py")
    try:
        subprocess.Popen([sys.executable, script, "--db", DB_PATH],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({"mensagem": "Automação iniciada", "status": "rodando"})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/api/automacao/status", methods=["GET"])
def status_automacao():
    conn = get_db()
    ultima = row_to_dict(conn.execute(
        "SELECT * FROM execucoes ORDER BY id DESC LIMIT 1").fetchone())
    conn.close()
    return jsonify(ultima or {"status": "nunca executado"})

@app.route("/api/execucoes", methods=["GET"])
def listar_execucoes():
    conn = get_db()
    rows = rows_to_list(conn.execute(
        "SELECT * FROM execucoes ORDER BY id DESC LIMIT 20").fetchall())
    conn.close()
    return jsonify(rows)



# ─── Rotas: Portal do Cliente ─────────────────────────────────────────────────

@app.route("/cliente/<token>")
def portal_cliente(token):
    """Página pública do cliente — acessada pelo link único."""
    portal_path = _os.path.join(_os.path.dirname(__file__), '..', 'frontend', 'cliente.html')
    with open(portal_path, 'r', encoding='utf-8') as f:
        return f.read()

@app.route("/api/cliente/<token>", methods=["POST"])
def api_portal_cliente(token):
    """Valida CPF e retorna dados da declaração."""
    data = request.json
    cpf_digitado = data.get("cpf", "").strip()
    
    # Normalizar CPF (remover pontos e traço)
    import re
    cpf_limpo = re.sub(r'[^0-9]', '', cpf_digitado)
    
    conn = get_db()
    # Buscar cliente pelo token (token = primeiros 8 chars do MD5 do CPF)
    clientes_rows = conn.execute("SELECT * FROM clientes").fetchall()
    cliente = None
    for row in clientes_rows:
        import hashlib
        cpf_row = re.sub(r'[^0-9]', '', row['cpf'])
        token_row = hashlib.md5(cpf_row.encode()).hexdigest()[:12]
        if token_row == token:
            # Verificar CPF digitado
            if cpf_limpo == cpf_row or cpf_digitado == row['cpf']:
                cliente = dict(row)
            break
    
    if not cliente:
        conn.close()
        return jsonify({"erro": "CPF não confere ou cliente não encontrado"}), 403
    
    decl = row_to_dict(conn.execute(
        "SELECT * FROM declaracoes WHERE cliente_id=? AND ano_exercicio=?",
        (cliente['id'], cliente['ano'])
    ).fetchone())
    
    historico = rows_to_list(conn.execute(
        "SELECT status, detalhe, data, origem FROM historico WHERE decl_id=? ORDER BY data ASC",
        (decl['id'],)
    ).fetchall()) if decl else []
    
    conn.close()
    
    return jsonify({
        "nome": cliente['nome'],
        "cpf": cliente['cpf'],
        "ano": cliente['ano'],
        "status": decl['status'] if decl else "Não encontrada",
        "detalhe": decl['detalhe'] if decl else "",
        "lote": decl['lote'] if decl else None,
        "valor_restit": decl['valor_restit'] if decl else None,
        "ultima_consulta": decl['ultima_consulta'] if decl else None,
        "historico": historico,
    })

@app.route("/api/cliente-link/<int:cid>", methods=["GET"])
def gerar_link_cliente(cid):
    """Gera o link único do cliente."""
    import hashlib, re
    conn = get_db()
    cliente = row_to_dict(conn.execute("SELECT cpf FROM clientes WHERE id=?", (cid,)).fetchone())
    conn.close()
    if not cliente:
        return jsonify({"erro": "Cliente não encontrado"}), 404
    cpf_limpo = re.sub(r'[^0-9]', '', cliente['cpf'])
    token = hashlib.md5(cpf_limpo.encode()).hexdigest()[:12]
    return jsonify({"token": token, "link": f"/cliente/{token}"})




# ─── Rotas: Anexos (Malha Fina) ──────────────────────────────────────────────

@app.route("/api/clientes/<int:cid>/anexos", methods=["GET"])
def listar_anexos(cid):
    conn = get_db()
    rows = rows_to_list(conn.execute(
        "SELECT id, nome, tipo, descricao, criado_em FROM anexos WHERE cliente_id=? ORDER BY criado_em DESC", (cid,)
    ).fetchall())
    conn.close()
    return jsonify(rows)

@app.route("/api/clientes/<int:cid>/anexos", methods=["POST"])
def upload_anexo(cid):
    import base64
    data = request.json
    nome = data.get("nome", "arquivo")
    tipo = data.get("tipo", "")
    descricao = data.get("descricao", "")
    dados_b64 = data.get("dados", "")
    try:
        dados = base64.b64decode(dados_b64)
    except Exception:
        return jsonify({"erro": "Dados inválidos"}), 400
    conn = get_db()
    conn.execute("INSERT INTO anexos (cliente_id,nome,tipo,dados,descricao) VALUES (?,?,?,?,?)",
                 (cid, nome, tipo, dados, descricao))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Anexo salvo"}), 201

@app.route("/api/anexos/<int:aid>", methods=["GET"])
def baixar_anexo(aid):
    import base64
    from flask import Response
    conn = get_db()
    row = conn.execute("SELECT nome, tipo, dados FROM anexos WHERE id=?", (aid,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"erro": "Não encontrado"}), 404
    return Response(
        row["dados"],
        mimetype=row["tipo"] or "application/octet-stream",
        headers={"Content-Disposition": f"attachment;filename={row['nome']}"}
    )

@app.route("/api/anexos/<int:aid>", methods=["DELETE"])
def deletar_anexo(aid):
    conn = get_db()
    conn.execute("DELETE FROM anexos WHERE id=?", (aid,))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Removido"})


# ─── Configurações do sistema ─────────────────────────────────────────────────

@app.route("/api/config", methods=["GET"])
def get_config():
    conn = get_db()
    try:
        rows = {r["chave"]: r["valor"] for r in conn.execute("SELECT chave, valor FROM config").fetchall()}
    except Exception:
        rows = {}
    conn.close()
    return jsonify(rows)

@app.route("/api/config", methods=["POST"])
def set_config():
    data = request.json
    conn = get_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS config (
                chave TEXT PRIMARY KEY,
                valor TEXT
            );
        """)
        for chave, valor in data.items():
            conn.execute("INSERT OR REPLACE INTO config (chave, valor) VALUES (?,?)", (chave, valor))
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({"erro": str(e)}), 400
    conn.close()
    return jsonify({"mensagem": "Configurado!"})

# ─── Rotas: Anotações ────────────────────────────────────────────────────────

@app.route("/api/clientes/<int:cid>/anotacoes", methods=["GET"])
def listar_anotacoes(cid):
    conn = get_db()
    rows = rows_to_list(conn.execute(
        "SELECT * FROM anotacoes WHERE cliente_id=? ORDER BY criado_em DESC", (cid,)
    ).fetchall())
    conn.close()
    return jsonify(rows)

@app.route("/api/clientes/<int:cid>/anotacoes", methods=["POST"])
def criar_anotacao(cid):
    data = request.json
    conn = get_db()
    conn.execute("INSERT INTO anotacoes (cliente_id,texto,autor) VALUES (?,?,?)",
                 (cid, data.get("texto",""), data.get("autor","Escritório")))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Anotação salva"}), 201

@app.route("/api/anotacoes/<int:aid>", methods=["DELETE"])
def deletar_anotacao(aid):
    conn = get_db()
    conn.execute("DELETE FROM anotacoes WHERE id=?", (aid,))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Removida"})

# ─── Rotas: Lembretes ─────────────────────────────────────────────────────────

@app.route("/api/lembretes", methods=["GET"])
def listar_lembretes():
    conn = get_db()
    rows = rows_to_list(conn.execute("""
        SELECT l.*, c.nome as cliente_nome, c.cpf as cliente_cpf
        FROM lembretes l
        LEFT JOIN clientes c ON c.id = l.cliente_id
        WHERE l.concluido = 0
        ORDER BY l.data_alerta ASC, l.criado_em ASC
    """).fetchall())
    vencidos = rows_to_list(conn.execute("""
        SELECT l.*, c.nome as cliente_nome
        FROM lembretes l
        LEFT JOIN clientes c ON c.id = l.cliente_id
        WHERE l.concluido=0 AND l.data_alerta IS NOT NULL
              AND l.data_alerta < date('now')
        ORDER BY l.data_alerta ASC
    """).fetchall())
    conn.close()
    return jsonify({"lembretes": rows, "vencidos": vencidos})

@app.route("/api/lembretes", methods=["POST"])
def criar_lembrete():
    data = request.json
    conn = get_db()
    conn.execute("""INSERT INTO lembretes (cliente_id,titulo,descricao,data_alerta,tipo)
                    VALUES (?,?,?,?,?)""",
                 (data.get("cliente_id"), data.get("titulo",""),
                  data.get("descricao",""), data.get("data_alerta"),
                  data.get("tipo","geral")))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Lembrete criado"}), 201

@app.route("/api/lembretes/<int:lid>", methods=["PUT"])
def atualizar_lembrete(lid):
    data = request.json
    conn = get_db()
    if data.get("concluido") is not None:
        conn.execute("UPDATE lembretes SET concluido=? WHERE id=?",
                     (data["concluido"], lid))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Atualizado"})

@app.route("/api/lembretes/<int:lid>", methods=["DELETE"])
def deletar_lembrete(lid):
    conn = get_db()
    conn.execute("DELETE FROM lembretes WHERE id=?", (lid,))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Removido"})

# ─── Rotas: Usuários ──────────────────────────────────────────────────────────

@app.route("/api/usuarios", methods=["GET"])
def listar_usuarios():
    conn = get_db()
    rows = rows_to_list(conn.execute(
        "SELECT id,nome,email,perfil,ativo,criado_em FROM usuarios ORDER BY nome"
    ).fetchall())
    conn.close()
    return jsonify(rows)

@app.route("/api/usuarios", methods=["POST"])
def criar_usuario():
    import hashlib
    data = request.json
    senha_hash = hashlib.sha256(data.get("senha","").encode()).hexdigest()
    conn = get_db()
    try:
        conn.execute("INSERT INTO usuarios (nome,email,senha_hash,perfil) VALUES (?,?,?,?)",
                     (data["nome"], data["email"], senha_hash, data.get("perfil","visualizador")))
        conn.commit()
        return jsonify({"mensagem": "Usuário criado"}), 201
    except Exception as e:
        return jsonify({"erro": str(e)}), 400
    finally:
        conn.close()

@app.route("/api/usuarios/<int:uid>", methods=["PUT"])
def atualizar_usuario(uid):
    data = request.json
    conn = get_db()
    conn.execute("UPDATE usuarios SET nome=COALESCE(?,nome), perfil=COALESCE(?,perfil), ativo=COALESCE(?,ativo) WHERE id=?",
                 (data.get("nome"), data.get("perfil"), data.get("ativo"), uid))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Atualizado"})

@app.route("/api/usuarios/<int:uid>", methods=["DELETE"])
def deletar_usuario(uid):
    conn = get_db()
    conn.execute("DELETE FROM usuarios WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Removido"})

# ─── Rotas: WhatsApp / Exportar ───────────────────────────────────────────────

@app.route("/api/whatsapp/<int:cid>", methods=["GET"])
def gerar_msg_whatsapp(cid):
    conn = get_db()
    cliente = row_to_dict(conn.execute("SELECT * FROM clientes WHERE id=?", (cid,)).fetchone())
    decl = row_to_dict(conn.execute(
        "SELECT * FROM declaracoes WHERE cliente_id=? AND ano_exercicio=?",
        (cid, cliente["ano"])).fetchone()) if cliente else None
    conn.close()
    if not cliente or not decl:
        return jsonify({"erro": "Não encontrado"}), 404

    status = decl.get("status","")
    nome_curto = cliente["nome"].split()[0].capitalize()
    msgs = {
        "Restituição": f"Olá {nome_curto}! 😊 Sua declaração de Imposto de Renda 2026 foi processada e você tem restituição a receber! {('Valor: ' + decl['valor_restit']) if decl.get('valor_restit') else ''} Em caso de dúvidas, estamos à disposição. Atenciosamente, Connex Soluções Contábeis.",
        "Transmitida": f"Olá {nome_curto}! Sua declaração de IR 2026 foi transmitida com sucesso para a Receita Federal e está em processamento. Assim que houver atualização, te avisamos! Atenciosamente, Connex Soluções Contábeis.",
        "Malha fina": f"Olá {nome_curto}! Identificamos que sua declaração de IR 2026 foi retida em malha fina pela Receita Federal. Por favor, entre em contato conosco o quanto antes para regularizarmos a situação. Atenciosamente, Connex Soluções Contábeis.",
        "IR a pagar": f"Olá {nome_curto}! Sua declaração de IR 2026 foi processada. Há imposto a pagar {('em ' + str(decl['quotas']) + ' quota(s) de ' + decl['valor_quota']) if decl.get('quotas') else ''}. Entre em contato para mais detalhes. Atenciosamente, Connex Soluções Contábeis.",
        "Aguardando envio": f"Olá {nome_curto}! Ainda não recebemos seus documentos para elaborar a declaração de IR 2026. Por favor, entre em contato para darmos andamento. Atenciosamente, Connex Soluções Contábeis.",
    }
    msg = msgs.get(status, f"Olá {nome_curto}! Segue atualização sobre sua declaração de IR 2026: {status}. Em caso de dúvidas, estamos à disposição. Atenciosamente, Connex Soluções Contábeis.")
    whats = cliente.get("whatsapp","")
    link = f"https://wa.me/55{whats.replace(' ','').replace('-','').replace('(','').replace(')','')}&text={msg}" if whats else ""
    return jsonify({"mensagem": msg, "link": link, "whatsapp": whats})

@app.route("/api/exportar/clientes", methods=["GET"])
def exportar_clientes():
    import csv, io
    conn = get_db()
    rows = conn.execute("""
        SELECT c.nome, c.cpf, c.ano, d.status, d.detalhe, d.valor_restit,
               d.quotas, d.valor_quota, d.valor_total_ir,
               c.honorario, c.forma_pgto, c.situacao_pgto, d.ultima_consulta
        FROM clientes c
        LEFT JOIN declaracoes d ON d.cliente_id=c.id AND d.ano_exercicio=c.ano
        ORDER BY c.nome
    """).fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Nome","CPF","Ano","Status","Detalhe","Restituição","Quotas","Valor/Quota","Total IR","Honorário","Forma Pgto","Sit. Pgto","Última Consulta"])
    for r in rows:
        writer.writerow(list(r))
    from flask import Response
    return Response(
        "﻿" + output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=clientes_ir2026.csv"}
    )


@app.route("/api/exportar/pdf", methods=["POST"])
def exportar_pdf():
    """Gera HTML formatado para impressão/PDF com filtros selecionados."""
    data = request.json
    status_selecionados = data.get("status", [])
    conn = get_db()

    query = """
        SELECT c.nome, c.cpf, c.ano, d.status, d.detalhe, d.valor_restit,
               d.quotas, d.valor_quota, d.valor_total_ir,
               c.honorario, c.forma_pgto, c.situacao_pgto
        FROM clientes c
        LEFT JOIN declaracoes d ON d.cliente_id=c.id AND d.ano_exercicio=c.ano
    """
    params = []
    if status_selecionados:
        placeholders = ",".join(["?" for _ in status_selecionados])
        query += f" WHERE d.status IN ({placeholders})"
        params = status_selecionados
    query += " ORDER BY c.nome"

    rows = [dict(r) for r in conn.execute(query, params).fetchall()]

    # Totais financeiros
    total_honor = sum(r["honorario"] or 0 for r in rows if r["honorario"])
    total_pago  = sum(r["honorario"] or 0 for r in rows if r["situacao_pgto"] == "Pago")
    total_pend  = sum(r["honorario"] or 0 for r in rows if r["situacao_pgto"] == "Aguardando Pgto")
    conn.close()

    from datetime import date
    hoje = date.today().strftime("%d/%m/%Y")
    status_label = ", ".join(status_selecionados) if status_selecionados else "Todos"

    linhas = ""
    for i, r in enumerate(rows, 1):
        cor = "#fff" if i % 2 == 0 else "#f9f9f9"
        linhas += f"""<tr style="background:{cor}">
            <td>{i}</td><td>{r['nome']}</td><td style="font-family:monospace">{r['cpf']}</td>
            <td><span style="padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;
                background:{'#EAF3DE' if r['status']=='Restituição' else '#FCEBEB' if r['status']=='Malha fina' else '#E6F1FB' if r['status'] in ['Transmitida','IR a pagar','Concluída'] else '#FAEEDA'};
                color:{'#3B6D11' if r['status']=='Restituição' else '#A32D2D' if r['status']=='Malha fina' else '#185FA5' if r['status'] in ['Transmitida','IR a pagar','Concluída'] else '#854F0B'}">
                {r['status'] or '—'}</span></td>
            <td>{r['valor_restit'] or ('R$ '+str(r['valor_total_ir'])) if r['valor_total_ir'] else '—'}</td>
            <td>{'R$ {:,.2f}'.format(r['honorario']).replace(',','X').replace('.',',').replace('X','.') if r['honorario'] else '—'}</td>
            <td>{'<span style="color:#3B6D11;font-weight:600">✓ Pago</span>' if r['situacao_pgto']=='Pago' else '<span style="color:#854F0B">Aguardando</span>' if r['situacao_pgto']=='Aguardando Pgto' else r['situacao_pgto'] or '—'}</td>
        </tr>"""

    html = f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
    <title>Relatório IR 2026 — Connex</title>
    <style>
        body{{font-family:Arial,sans-serif;font-size:12px;color:#1a1916;margin:0;padding:24px}}
        .header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:24px;padding-bottom:16px;border-bottom:2px solid #1a1916}}
        .logo{{font-size:18px;font-weight:700}}.logo small{{display:block;font-size:11px;font-weight:400;color:#6b6a65;margin-top:2px}}
        .meta{{text-align:right;font-size:11px;color:#6b6a65}}
        .resumo{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}}
        .card{{background:#f5f5f0;border-radius:8px;padding:12px;text-align:center}}
        .card-label{{font-size:10px;color:#6b6a65;margin-bottom:4px}}
        .card-val{{font-size:18px;font-weight:700}}
        table{{width:100%;border-collapse:collapse;font-size:11px}}
        th{{background:#1a1916;color:#fff;padding:8px 10px;text-align:left;font-weight:600}}
        td{{padding:7px 10px;border-bottom:1px solid #e8e8e0}}
        .footer{{margin-top:20px;padding-top:12px;border-top:1px solid #e8e8e0;font-size:10px;color:#a8a7a3;text-align:center}}
        @media print{{body{{padding:0}}button{{display:none}}}}
    </style></head><body>
    <div class="header">
        <div class="logo">Connex Soluções Contábeis<small>Relatório IRPF 2026 — {hoje}</small></div>
        <div class="meta">Filtro: {status_label}<br>Total: {len(rows)} clientes<br>Gerado em: {hoje}</div>
    </div>
    <div class="resumo">
        <div class="card"><div class="card-label">Total clientes</div><div class="card-val">{len(rows)}</div></div>
        <div class="card"><div class="card-label">Honorários lançados</div><div class="card-val" style="color:#185FA5">R$ {total_honor:,.2f}</div></div>
        <div class="card"><div class="card-label">Total recebido</div><div class="card-val" style="color:#3B6D11">R$ {total_pago:,.2f}</div></div>
        <div class="card"><div class="card-label">A receber</div><div class="card-val" style="color:#854F0B">R$ {total_pend:,.2f}</div></div>
    </div>
    <table><thead><tr><th>#</th><th>Cliente</th><th>CPF</th><th>Status</th><th>Valor IR</th><th>Honorário</th><th>Pgto</th></tr></thead>
    <tbody>{linhas}</tbody></table>
    <div class="footer">Connex Soluções Contábeis — Sistema IR Monitor — Documento gerado automaticamente em {hoje}</div>
    <script>window.onload=()=>setTimeout(()=>window.print(),500)</script>
    </body></html>"""

    from flask import Response
    return Response(html, mimetype="text/html")

# ─── Rotas: Relatórios ────────────────────────────────────────────────────────

@app.route("/api/relatorios", methods=["GET"])
def relatorios():
    conn = get_db()

    # Restituições
    restituicoes = rows_to_list(conn.execute("""
        SELECT c.id, c.nome, c.cpf, d.valor_restit, d.lote, d.detalhe, d.ultima_consulta
        FROM clientes c
        JOIN declaracoes d ON d.cliente_id=c.id AND d.ano_exercicio=c.ano
        WHERE d.status='Restituição'
        ORDER BY CAST(REPLACE(REPLACE(REPLACE(COALESCE(d.valor_restit,'0'),'R$',''),' ',''),',','.') AS REAL) DESC
    """).fetchall())

    # IR a pagar
    ir_pagar = rows_to_list(conn.execute("""
        SELECT c.id, c.nome, c.cpf, d.valor_total_ir, d.quotas, d.valor_quota, d.detalhe, d.ultima_consulta
        FROM clientes c
        JOIN declaracoes d ON d.cliente_id=c.id AND d.ano_exercicio=c.ano
        WHERE d.status='IR a pagar'
        ORDER BY c.nome
    """).fetchall())

    # Malha fina
    malha = rows_to_list(conn.execute("""
        SELECT c.id, c.nome, c.cpf, d.detalhe, d.ultima_consulta
        FROM clientes c
        JOIN declaracoes d ON d.cliente_id=c.id AND d.ano_exercicio=c.ano
        WHERE d.status='Malha fina'
        ORDER BY c.nome
    """).fetchall())

    # Resumo por status
    por_status = rows_to_list(conn.execute("""
        SELECT d.status, COUNT(*) as qtd
        FROM declaracoes d JOIN clientes c ON c.id=d.cliente_id AND d.ano_exercicio=c.ano
        GROUP BY d.status ORDER BY qtd DESC
    """).fetchall())

    conn.close()
    return jsonify({
        "restituicoes": restituicoes,
        "ir_pagar": ir_pagar,
        "malha": malha,
        "por_status": por_status,
    })

# ─── Rotas: Financeiro ────────────────────────────────────────────────────────

@app.route("/api/financeiro", methods=["GET"])
def financeiro():
    conn = get_db()
    stats = row_to_dict(conn.execute("""
        SELECT
            COUNT(*) as total_clientes,
            COUNT(CASE WHEN d.status IN ('Transmitida','Restituição','IR a pagar','Concluída') THEN 1 END) as declaracoes_entregues,
            COUNT(CASE WHEN d.status NOT IN ('Transmitida','Restituição','IR a pagar','Concluída','Isento','Não obrigado') THEN 1 END) as declaracoes_pendentes,
            COUNT(CASE WHEN d.status = 'Isento' OR d.status = 'Não obrigado' THEN 1 END) as isentos,
            COUNT(CASE WHEN c.honorario IS NOT NULL AND c.honorario > 0 THEN 1 END) as com_honorario,
            COALESCE(SUM(CASE WHEN c.honorario IS NOT NULL THEN c.honorario ELSE 0 END), 0) as total_lancado,
            COALESCE(SUM(CASE WHEN c.situacao_pgto='Pago' THEN c.honorario ELSE 0 END), 0) as total_pago,
            COALESCE(SUM(CASE WHEN c.situacao_pgto='Aguardando Pgto' THEN c.honorario ELSE 0 END), 0) as total_pendente,
            COUNT(CASE WHEN c.situacao_pgto='Pago' THEN 1 END) as qtd_pago,
            COUNT(CASE WHEN c.situacao_pgto='Aguardando Pgto' THEN 1 END) as qtd_pendente,
            COUNT(CASE WHEN c.situacao_pgto='Nao se Aplica' THEN 1 END) as qtd_nao_aplica
        FROM clientes c
        LEFT JOIN declaracoes d ON d.cliente_id = c.id AND d.ano_exercicio = c.ano
    """).fetchone())

    por_forma = rows_to_list(conn.execute("""
        SELECT forma_pgto, COUNT(*) as qtd,
               SUM(CASE WHEN honorario IS NOT NULL THEN honorario ELSE 0 END) as total
        FROM clientes WHERE forma_pgto IS NOT NULL AND forma_pgto != 'Nao se Aplica'
        GROUP BY forma_pgto ORDER BY total DESC
    """).fetchall())

    clientes_pendentes = rows_to_list(conn.execute("""
        SELECT c.id, c.nome, c.cpf, c.honorario, c.forma_pgto, c.situacao_pgto, d.status
        FROM clientes c
        LEFT JOIN declaracoes d ON d.cliente_id = c.id AND d.ano_exercicio = c.ano
        WHERE c.situacao_pgto = 'Aguardando Pgto'
        ORDER BY c.honorario DESC
    """).fetchall())

    clientes_pagos = rows_to_list(conn.execute("""
        SELECT c.id, c.nome, c.cpf, c.honorario, c.forma_pgto, c.situacao_pgto, d.status
        FROM clientes c
        LEFT JOIN declaracoes d ON d.cliente_id = c.id AND d.ano_exercicio = c.ano
        WHERE c.situacao_pgto = 'Pago'
        ORDER BY c.nome
    """).fetchall())

    conn.close()
    return jsonify({
        "stats": stats,
        "por_forma": por_forma,
        "pendentes": clientes_pendentes,
        "pagos": clientes_pagos,
    })

@app.route("/api/clientes/<int:cid>/financeiro", methods=["PUT"])
def atualizar_financeiro(cid):
    data = request.json
    conn = get_db()
    conn.execute("""
        UPDATE clientes SET honorario=?, forma_pgto=?, situacao_pgto=? WHERE id=?
    """, (data.get("honorario"), data.get("forma_pgto"), data.get("situacao_pgto"), cid))
    conn.commit()
    conn.close()
    return jsonify({"mensagem": "Financeiro atualizado"})

# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("✅ IR Monitor Backend rodando em http://localhost:5000")
    app.run(debug=True, port=5000)
