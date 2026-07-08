# Plano: Formato por Registrador

## Objetivo

Permitir configurar o formato de exibição (Bin, Dec, Hex, Float) de cada registrador individualmente, em vez de um único formato para toda a grid.

Exemplo de uso:

```
Reg 0+1 → Float (ABCD)
Reg 2   → Bin
Reg 3   → Hex
Reg 4+5 → Float (DCBA)
Reg 6   → Dec (signed)
```

## Estado Atual

| Componente | Behavior |
|---|---|
| `Base` enum | Bin, Dec, Hex, Float — um valor para toda a grid |
| `RegistersModel.base` | Um único `Base` aplicado a todas as células |
| `format_value()` | Recebe `base` como parâmetro — mesmo formato para todos |
| `_max_length()` | Um valor para todos os campos editáveis |
| `_parse_edit_value()` | Uma validação para todos |
| `_build_text_field()` | Um `validate_field` closure para todos |
| `collect_write_values()` | Percorre todos os registradores igualmente |
| `data_format_dropdown` | Um dropdown na UI controlando tudo |
| `Settings.base` | Um int no INI |

## Arquitetura Proposta

### 1. Modelo de Dados — `format_map`

Substituir o `base` único por um mapa endereço → formato:

```python
# formato padrão aplicado a qualquer endereço não listado
DEFAULT_FORMAT = Base.Dec

# exemplo de mapa
format_map: dict[int, Base] = {
    0: Base.Float,   # regs 0+1 → float (consome 2 registradores)
    2: Base.Bin,    # reg 2 → binário
    3: Base.Hex,    # reg 3 → hex
    4: Base.Float,  # regs 4+5 → float
}
```

Para o Float, o registrador N consome automaticamente o registrador N+1. O N+1 fica marcado como continuação (`—`) e não pode ter formato próprio.

### 2. Mudanças por Arquivo

#### `src/fmodmaster/registers.py`

**`RegistersModel.__init__`**

- Remover parâmetro `base: BaseValue` único
- Adicionar `default_base: BaseValue = Base.Dec` (fallback para endereços sem configuração)
- Adicionar `format_map: dict[int, Base] | None = None` (mapa opcional)
- Adicionar `float_endian_map: dict[int, FloatEndian] | None = None` (permite endian diferente por float)
- Manter `float_endian: FloatEndian` como fallback default para floats sem endian específico

**Propriedade `is_float_mode` → substituir por método**

```python
def _format_for(self, address: int, value_index: int) -> Base:
    return self.format_map.get(address, self.default_base)
```

**`_used_cell`**

- Hoje: `format_value(value, self.base, ...)`
- Passa a ser: checar `self._format_for(address)` e formatar conforme o formato daquele endereço
- Se Float: aplicar `_wrap_float_cell` para aquele endereço específico
- Se Bin/Dec/Hex: formatar com `format_value` usando o base daquele endereço

**`_wrap_float_cell`**

- Hoje: aplicado a todas as células quando `self.base is Base.Float`
- Passa a ser: aplicado apenas às células cujo `format_map[address] == Base.Float`
- O registrador N+1 de um float fica como `—` (continuação) e **não editável**

**`_max_length`**

- Hoje: retorna um valor baseado em `self.base`
- Passa a ser: `_max_length_for(self, address: int) -> int | None` — retorna conforme o formato do endereço

**`_parse_edit_value`**

- Hoje: valida conforme `self.base`
- Passa a ser: `_parse_edit_value_for(self, raw: str, address: int)` — chama `_parse_edit_float` para floats, validação bin/dec/hex conforme o formato daquele endereço

**`_build_text_field`**

- O `validate_field` closure precisa saber o formato da célula
- Cada TextEditField cria um closure que valida conforme o formato do endereço

**`collect_write_values`**

- Hoje: percorre todos os endereços igualmente (ou todos como float)
- Passa a ser: percorre endereço por endereço; se float, consome 2 valores; senão, consome 1

**`format_value`**

- Sem mudança — continua recebendo `base` como parâmetro
- A mudança é quem chama (agora passa o base por endereço)

#### `src/fmodmaster/main_view.py`

**Remover `data_format_dropdown` como controle global**

- Substituir por um controle de "formato padrão" (aplica a registradores sem configuração específica)
- O dropdown continua existindo mas controla apenas o **default**, não todos os registradores

**Adicionar context menu por registrador (clique direito na célula)**

Usar `ft.ContextMenu` do Flet — clique direito direto na célula da grid abre um menu com as opções de formato. Sem botão extra na interface.

```
Clique direito no registrador 5
┌──────────────────┐
│ Format as:       │
│   Dec (default)  │
│   Bin            │
│   Hex            │
│   ─────────────  │
│   Float          │
│     ABCD (BE_BE) │
│     DCBA (LE_LE) │
│     BADC (BE_LE) │
│     CDAB (LE_BE) │
│   ─────────────  │
│   Reset to default│
└──────────────────┘
```

Implementação: cada célula da grid é envolvida por `ft.ContextMenu` com `ft.MenuItem` para cada formato. O handler do menu item atualiza o `format_map` com o endereço da célula clicada e reconstrói a grid.

- **Sem botão extra** na request area
- `data_format_dropdown` continua existindo como "Default Format" (aplicado a registradores sem configuração específica)
- Para floats, o submenu mostra os 4 endians
- "Reset to default" remove o endereço do `format_map` (volta a usar o default)

**`_build_grid`**

- Passar `format_map` e `default_base` para `build_grid`

**`_refresh_controls`**

- Remover lógica de `float_endian_dropdown.visible` (já movido para Settings)
- O `signed_checkbox` passa a ser por registrador também (ou mantém como default)

#### `src/fmodmaster/config.py`

**Persistir `format_map` no `.fmmsess`**

Nova seção no INI:

```ini
[RegisterFormats]
0=3
2=2
3=16
4=3

[RegisterFloatEndians]
0=0
4=1

[Session]
...
DefaultBase=1
```

- `RegisterFormats`: endereço → valor do enum `Base`
- `RegisterFloatEndians`: endereço → valor do enum `FloatEndian` (só para floats com endian não-default)
- `DefaultBase`: formato padrão para registradores não listados
- Manter `Base` legado como fallback (compatibilidade com .fmmsess antigos)

#### `tests/test_registers.py`

Novos testes necessários:

| Teste | Descrição |
|---|---|
| `test_format_map_default_for_unmapped_address` | Endereço não listado usa default_base |
| `test_format_map_float_spans_two_registers` | Float no reg 0 marca reg 1 como continuação |
| `test_format_map_mixed_formats_in_same_row` | Reg 0 float, reg 2 bin, reg 3 hex na mesma linha |
| `test_format_map_float_continuation_not_editable` | Reg 1 (continuação) não é editável |
| `test_format_map_float_prevents_next_register_config` | Tentar configurar reg 1 quando reg 0 é float → bloqueia |
| `test_format_map_collect_write_values_mixed` | Write coleta 2 regs para float, 1 reg para bin/dec/hex |
| `test_format_map_per_register_max_length` | max_length diferente por formato da célula |
| `test_format_map_per_register_validation` | Validação float/bin/dec/hex por endereço |
| `test_format_map_float_endian_per_register` | Reg 0 float ABCD, reg 4 float DCBA |
| `test_format_map_persist_load_save` | Salva e carrega format_map do .fmmsess |
| `test_format_map_odd_qty_last_register_non_float` | Último reg de qty ímpar sem float → fallback int |

### 3. Restrições e Validações

#### Float divide registradores

Se o registrador N é Float:
- N automaticamente consome N+1
- N+1 fica como `—` (continuação, não editável)
- N+1 **não pode** ter formato próprio configurado
- Tentar configurar N+1 → bloquear com mensagem de erro ou ignorar silenciosamente

#### Conflito de Float sobreposto

Se o usuário configura:
- Reg 0 → Float (consome 0+1)
- Reg 1 → Hex

Isso é um conflito. Opções de tratamento:
- **Opção A**: Rejeitar a configuração do reg 1 com erro
- **Opção B**: Aceitar mas ignorar o reg 1 (o Float do reg 0 tem precedência)
- **Opção C**: Rejeitar o Float no reg 0 se o reg 1 já tem formato próprio

Recomendação: **Opção A** — rejeitar com snackbar "Register 1 is consumed by float at address 0".

### 4. Fluxo de UI Proposto

1. Usuário abre o app — grid mostra todos os registradores no formato default (Dec)
2. Usuário clica com o **botão direito** no registrador 0 → abre context menu
3. Usuário seleciona "Float → ABCD (BE_BE)" → reg 0+1 passa a exibir float, reg 1 vira `—`
4. Usuário clica com o direito no registrador 2 → seleciona "Bin"
5. Usuário clica com o direito no registrador 3 → seleciona "Hex"
6. Usuário clica com o direito no registrador 1 → context menu mostra "Register 1 is consumed by float at address 0" (opção desabilitada)
7. Grid reconstrói com formatos mistos automaticamente após cada seleção
8. Usuário salva sessão → format_map persiste no .fmmsess
9. Usuário carrega sessão → format_map restaura

### 5. Estimativa de Complexidade

| Arquivo | Esforço | Descrição |
|---|---|---|
| `registers.py` | **Alto** | Refactor do modelo, múltiplas validações, coleta de writes |
| `main_view.py` | **Médio** | Context menu por célula, passar format_map, remover lógica global |
| `config.py` | **Baixo** | Nova seção INI, save/load do mapa |
| `tests/test_registers.py` | **Médio** | ~11 novos testes |
| **Total** | **Médio-Alto** | ~2-3 horas de implementação focada |

### 6. Riscos

| Risco | Mitigação |
|---|---|
| Float sobreposto (reg 0 e reg 1 ambos float) | Validar e rejeitar antes de aplicar |
| Qty ímpar com float no último par | Fallback para int no reg solitário (já implementado) |
| Performance com mapa grande | `dict[int, Base]` é O(1) lookup, irrelevante |
| Compatibilidade com .fmmsess antigo | `format_map` vazio → usa `base` legado do INI |
| Context menu no Flet | `ft.ContextMenu` + `ft.MenuItem` é nativo do Flet 0.85 |

### 7. Ordem de Implementação

1. **`registers.py`**: `format_map` no modelo + `_format_for(address)` + ajustar `_used_cell`, `_max_length`, `_parse_edit_value`, `collect_write_values`
2. **Testes do modelo**: validar formatos mistos, floats sobrepostos, coleta de writes
3. **`config.py`**: persistência do `format_map` no INI
4. **`main_view.py`**: `ft.ContextMenu` por célula da grid + passar `format_map` para `build_grid`
5. **Testes de integração**: validar fluxo completo de UI
