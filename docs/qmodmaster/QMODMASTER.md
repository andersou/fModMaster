# Levantamento funcional do projeto qModMaster

## 1. Visão geral

O **qModMaster** é uma aplicação desktop escrita em **C++ com Qt**, cujo objetivo é atuar como um **mestre Modbus** com interface gráfica para comunicação com dispositivos escravos via **Modbus RTU** e **Modbus TCP**. O projeto declara explicitamente que fornece uma GUI para comunicação RTU/TCP e inclui um **Bus Monitor** para examinar todo o tráfego Modbus no barramento.

O projeto utiliza a biblioteca **libmodbus 3.1.0-1** para executar as operações Modbus e a biblioteca **QsLog** para logging. A compilação original é baseada em **Qt 5.2.1** via arquivo `QModMaster.pro`. O fork analisado também se descreve como ajustado para compilar com **Qt 5 no macOS**, embora o README original mencione suporte a Windows e Linux.

A aplicação é centrada em uma janela principal onde o usuário escolhe o modo de comunicação, configura o escravo, seleciona uma função Modbus, define o endereço inicial, o número de coils/registers, o formato de visualização dos dados e executa leituras, escritas ou varreduras periódicas.

---

## 2. Stack e arquitetura geral

### 2.1 Tecnologias principais

| Área                  | Tecnologia                                |
| --------------------- | ----------------------------------------- |
| Linguagem             | C++                                       |
| Framework gráfico     | Qt Widgets                                |
| Build                 | QMake / `.pro`                            |
| Comunicação Modbus    | libmodbus                                 |
| Logging               | QsLog                                     |
| Interface             | Arquivos `.ui` do Qt Designer             |
| Internacionalização   | Qt translations / `.ts` / `.qm`           |
| Plataformas previstas | Windows, Linux e fork ajustado para macOS |

O arquivo de projeto declara dependência dos módulos Qt `core`, `gui` e `network`, além de `widgets` em versões mais novas do Qt. Também inclui diretamente os fontes da `libmodbus` e do `QsLog`, indicando que o projeto não depende apenas de bibliotecas externas instaladas no sistema: ele carrega parte importante das dependências dentro do próprio repositório.

### 2.2 Estrutura de diretórios

| Caminho                | Função                                                                                                                 |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `src/`                 | Código principal da aplicação: janela principal, adapter Modbus, modelos de dados, utilitários, monitor e ferramentas. |
| `forms/`               | Telas Qt Designer: janela principal, configurações, monitor, ferramentas e about.                                      |
| `3rdparty/libmodbus/`  | Fontes da libmodbus usados pelo projeto.                                                                               |
| `3rdparty/QsLog/`      | Biblioteca de logging.                                                                                                 |
| `icons/`               | Ícones e recursos visuais.                                                                                             |
| `translations/`        | Arquivos de tradução.                                                                                                  |
| `Docs/` e `ManModbus/` | Documentação/manual auxiliar.                                                                                          |
| `build/`               | Arquivos auxiliares de build.                                                                                          |

Essa divisão aparece diretamente na árvore do repositório e no arquivo `.pro`, que lista os fontes, headers, forms, translations e recursos carregados na aplicação.

---

## 3. Componentes principais

## 3.1 `main.cpp`

O ponto de entrada da aplicação cria o `QApplication`, ativa suporte a **High DPI Scaling**, carrega o tradutor conforme o idioma do sistema, configura o logger, instancia `ModbusAdapter`, `ModbusCommSettings` e a `MainWindow`.

Responsabilidades:

* Inicializar a aplicação Qt.
* Carregar tradução baseada no locale.
* Configurar logging em console/debug e arquivo `QModMaster.log`.
* Criar o objeto central de comunicação Modbus.
* Carregar as configurações de `qModMaster.ini`.
* Criar e exibir a janela principal.
* Conectar sinais de atualização do adapter com a interface.

Fluxo resumido:

1. Cria `QApplication`.
2. Configura organização, domínio e nome da aplicação.
3. Carrega tradução.
4. Cria logger.
5. Define destino do log em arquivo.
6. Instancia `ModbusAdapter`.
7. Instancia `ModbusCommSettings`.
8. Cria `MainWindow`.
9. Conecta sinais como atualização de registros e contadores.
10. Exibe a janela.

---

## 3.2 `MainWindow`

A `MainWindow` é o controlador principal da interface. Ela concentra os eventos de UI, menus, toolbar, ações de conexão, leitura, escrita, scan, troca de idioma, abertura de diálogos, carga/salvamento de sessão e atualização visual dos dados.

Responsabilidades principais:

* Inicializar a tela com os valores salvos em configuração.
* Gerenciar os botões `Connect`, `Read/Write`, `Scan`, `Clear`, `Reset Counters`.
* Abrir os diálogos de configuração RTU, TCP e gerais.
* Abrir o Bus Monitor.
* Abrir a janela Tools.
* Controlar o estado da interface quando conectado/desconectado.
* Controlar o estado da interface durante scan periódico.
* Ajustar os limites de campos conforme a função Modbus selecionada.
* Atualizar o modelo da tabela de registradores/coils.
* Atualizar labels de status: conexão, base address, pacotes e erros.
* Abrir arquivo de log.
* Abrir manual Modbus.
* Alternar idioma.
* Salvar e carregar sessão.

A janela principal contém menus `File`, `Options`, `Help`, `View` e `Commands`, além de ações para sair, configurar RTU/TCP, conectar, ler/escrever, escanear, abrir monitor, abrir ferramentas, resetar contadores, limpar tabela, abrir log, carregar/salvar sessão e alternar idiomas.

---

## 3.3 `ModbusAdapter`

O `ModbusAdapter` é a camada de comunicação entre a interface e a `libmodbus`. Ele encapsula conexão, desconexão, leitura, escrita, polling, contadores, tratamento de erros e registro bruto de mensagens para o Bus Monitor.

Responsabilidades:

* Criar contexto Modbus RTU.
* Criar contexto Modbus TCP.
* Conectar e desconectar.
* Configurar slave ID.
* Configurar timeout.
* Executar transações Modbus.
* Executar leitura de coils/registers.
* Executar escrita de coils/registers.
* Gerenciar timer de polling.
* Manter contadores de pacotes e erros.
* Preencher `RegistersModel`.
* Preencher `RawDataModel`.
* Reportar mensagens de erro para a interface.
* Registrar mensagens Tx/Rx com timestamp.
* Fazer flush do contexto Modbus em caso de erro.
* Executar callbacks da libmodbus para monitoramento bruto.

O adapter mantém buffers internos para até **2000 coils/discrete inputs** e até **125 registers**, alinhado aos limites usados na interface para as funções Modbus suportadas.

---

## 3.4 `ModbusCommSettings`

`ModbusCommSettings` centraliza as configurações persistentes da aplicação. Ele herda de `QSettings` e grava/lê dados em `qModMaster.ini`, além de salvar e carregar arquivos de sessão `.ses`.

Responsabilidades:

* Carregar configurações gerais.
* Salvar configurações gerais.
* Carregar sessão.
* Salvar sessão.
* Persistir parâmetros TCP.
* Persistir parâmetros RTU.
* Persistir parâmetros de interface.
* Persistir último modo Modbus usado.
* Persistir função, endereço, quantidade, formato e scan rate.

Configurações persistidas:

| Grupo     | Chaves                                                                               |
| --------- | ------------------------------------------------------------------------------------ |
| `TCP`     | `TCPPort`, `SlaveIP`                                                                 |
| `RTU`     | `SerialDev`, `SerialPort`, `Baud`, `DataBits`, `StopBits`, `Parity`, `RTS`           |
| `Var`     | `MaxNoOfLines`, `BaseAddr`, `TimeOut`, `LoggingLevel`                                |
| `Session` | `ModBusMode`, `SlaveID`, `ScanRate`, `FunctionCode`, `StartAddr`, `NoOfRegs`, `Base` |

Valores padrão importantes:

| Configuração          | Padrão            |
| --------------------- | ----------------- |
| TCP port              | `502`             |
| Slave IP              | `127.000.000.001` |
| Baud rate RTU         | `9600`            |
| Data bits             | `8`               |
| Stop bits             | `1`               |
| Parity                | `None`            |
| Max bus monitor lines | `60`              |
| Base address          | `0`               |
| Slave ID              | `1`               |
| Scan rate             | `1000 ms`         |
| Function code inicial | `Read Coils`      |
| Formato inicial       | Decimal           |

---

## 3.5 `RegistersModel`

O `RegistersModel` representa a tabela de coils/registers exibida na tela principal. Ele é baseado em `QStandardItemModel` e usa um delegate próprio para controlar a edição dos valores.

Responsabilidades:

* Criar a grade de dados.
* Organizar endereços em colunas `00` a `09`.
* Criar linhas baseadas no endereço inicial.
* Marcar células não utilizadas como `x`.
* Marcar células utilizadas sem valor como `-`.
* Exibir valores lidos.
* Permitir edição apenas em funções de escrita.
* Converter valores quando o usuário troca o formato entre binário, decimal e hexadecimal.
* Exibir tooltip com endereço da célula.
* Suportar valores signed/unsigned.
* Suportar dados de 1 bit ou 16 bits.

A grade é montada em blocos de 10 colunas. O posicionamento considera `startAddress % 10`, permitindo que a tabela reflita visualmente o alinhamento do endereço inicial.

---

## 3.6 `RegistersDataDelegate`

O `RegistersDataDelegate` controla como os valores da tabela são editados. Ele cria editores diferentes dependendo do tipo de dado e da base selecionada.

Comportamentos:

| Formato              | Editor                                               |
| -------------------- | ---------------------------------------------------- |
| Binário para 16 bits | `QLineEdit` com máscara de 16 bits                   |
| Binário para coil    | `QSpinBox` limitado a `0` ou `1`                     |
| Decimal              | `QLineEdit` com regex para número com sinal opcional |
| Hexadecimal          | `QLineEdit` com máscara hexadecimal                  |

Validações:

* Valor máximo aceito: `65535`.
* Valor mínimo signed aceito: `-32768`.
* Valores inválidos geram aviso por `InfoBar`.
* A conversão considera se o campo está em modo signed ou unsigned.

---

## 3.7 `RawDataModel`

O `RawDataModel` armazena linhas textuais de tráfego bruto para o Bus Monitor. Ele usa `QStringListModel` e possui limite configurável de linhas.

Responsabilidades:

* Adicionar linhas Tx/Rx/Sys.
* Limitar o número máximo de linhas.
* Remover linhas antigas quando o limite é excedido.
* Limpar o conteúdo.
* Ativar ou desativar captura de linhas.
* Servir como modelo da lista exibida pelo Bus Monitor.

O monitoramento bruto pode ser habilitado ou desabilitado, e a própria janela do Bus Monitor ativa a captura quando é exibida e desativa quando é fechada.

---

## 3.8 `BusMonitor`

O `BusMonitor` é uma janela auxiliar não modal que mostra o tráfego Modbus bruto e interpreta mensagens Tx/Rx/Sys selecionadas.

Funcionalidades:

* Exibir linhas brutas de comunicação.
* Diferenciar mensagens `Sys`, `Tx` e `Rx`.
* Mostrar timestamp.
* Salvar log bruto em arquivo texto.
* Limpar monitor.
* Interpretar ADU/PDU.
* Interpretar Modbus RTU.
* Interpretar Modbus TCP.
* Exibir campos de cabeçalho RTU/TCP.
* Exibir function code.
* Exibir endereço inicial.
* Exibir quantidade de coils/registers.
* Exibir byte count.
* Exibir valores enviados ou recebidos.
* Identificar respostas de exceção Modbus.

Para RTU, o monitor interpreta slave address, PDU e CRC. Para TCP, interpreta transaction ID, protocol ID, length, unit ID e PDU.

---

## 3.9 `Tools`

A janela `Tools` oferece comandos auxiliares de diagnóstico e conectividade.

Funcionalidades:

| Ferramenta      | Modo    | Descrição                                                               |
| --------------- | ------- | ----------------------------------------------------------------------- |
| Report Slave ID | RTU/TCP | Executa `modbus_report_slave_id` e mostra ID e status do dispositivo.   |
| Ping            | TCP     | Executa comando de ping para o IP configurado.                          |
| Port Status     | TCP     | Usa `QTcpSocket` para verificar se a porta TCP configurada está aberta. |

A ferramenta ajusta os comandos disponíveis conforme o modo selecionado. Em modo RTU/TCP, mantém o comando de diagnóstico Modbus. Em modo TCP, também habilita `Ping` e `Port Status`.

---

## 3.10 `EUtils`

`EUtils` concentra funções utilitárias e mapeamentos usados pela aplicação.

Responsabilidades:

* Mapear function codes Modbus para nomes.
* Mapear nomes para function codes.
* Identificar se uma função é de escrita.
* Identificar se uma função escreve coils.
* Identificar se uma função escreve registers.
* Formatar valores em binário, decimal ou hexadecimal.
* Tratar representação signed/unsigned.
* Gerar timestamps.
* Gerar prefixos `Sys`, `Tx`, `Rx`.
* Converter paridade para caractere usado pela libmodbus.
* Traduzir erros comuns da libmodbus para mensagens mais amigáveis.

Function codes suportados:

| Nome                     | Código |
| ------------------------ | ------ |
| Read Coils               | `0x01` |
| Read Discrete Inputs     | `0x02` |
| Read Holding Registers   | `0x03` |
| Read Input Registers     | `0x04` |
| Write Single Coil        | `0x05` |
| Write Single Register    | `0x06` |
| Write Multiple Coils     | `0x0F` |
| Write Multiple Registers | `0x10` |
| Report Server/Slave ID   | `0x11` |

---

# 4. Funcionalidades detalhadas

## 4.1 Inicialização da aplicação

Ao iniciar, a aplicação:

1. Cria o objeto `QApplication`.
2. Configura metadados da aplicação.
3. Ativa High DPI Scaling.
4. Carrega arquivo de tradução baseado no idioma do sistema.
5. Configura logging via QsLog.
6. Define o arquivo `QModMaster.log`.
7. Cria o adapter Modbus.
8. Carrega `qModMaster.ini`.
9. Cria a janela principal.
10. Conecta sinais entre adapter e UI.
11. Exibe a interface.

---

## 4.2 Logging

O projeto usa **QsLog** e permite configurar o nível de logging pelo arquivo `QModMaster.ini`. O README lista os níveis `0` a `6`, indo de `TraceLevel` até `OffLevel`.

Níveis:

| Valor | Nível |
| ----- | ----- |
| `0`   | Trace |
| `1`   | Debug |
| `2`   | Info  |
| `3`   | Warn  |
| `4`   | Error |
| `5`   | Fatal |
| `6`   | Off   |

A aplicação cria arquivo de log chamado `QModMaster.log`. Há também uma ação na interface para abrir esse arquivo diretamente.

---

## 4.3 Internacionalização

A aplicação possui suporte a tradução via `QTranslator`. O projeto inclui traduções para chinês simplificado e tradicional, além de ações de menu para alternância de idioma.

Idiomas previstos na interface:

* Inglês.
* Chinês simplificado.
* Chinês tradicional.

A troca de idioma recarrega o arquivo de tradução e reaplica os textos da UI.

---

## 4.4 Configuração Modbus RTU

A tela de configuração RTU permite ajustar parâmetros seriais.

Campos:

| Campo         | Descrição                                                      |
| ------------- | -------------------------------------------------------------- |
| Serial device | Prefixo/dispositivo serial, como `/dev/ttyS` ou `/dev/ttyUSB`. |
| Serial port   | Número da porta serial.                                        |
| Baud          | Velocidade de comunicação.                                     |
| Data bits     | Bits de dados.                                                 |
| Stop bits     | Bits de parada.                                                |
| Parity        | Paridade.                                                      |
| RTS           | Configuração de RTS, quando suportada.                         |

Baud rates disponíveis:

* `110`
* `300`
* `600`
* `1200`
* `2400`
* `4800`
* `9600`
* `14400`
* `19200`
* `38400`
* `56000`
* `57600`
* `115200`
* `128000`
* `153600`
* `230400`
* `256000`
* `460800`
* `921600`

Regras:

* Em Windows, o campo de dispositivo serial é desabilitado e o sistema usa padrão de porta COM.
* Em sistemas Unix-like, o usuário pode escolher o prefixo do device.
* Quando conectado, os campos de configuração RTU ficam desabilitados para evitar alteração durante comunicação ativa.
* A conexão RTU usa `modbus_new_rtu`.
* Depois de criar o contexto, o adapter define slave ID, error recovery, timeout e executa `modbus_connect`.

---

## 4.5 Configuração Modbus TCP

A tela TCP permite configurar IP do escravo e porta.

Campos:

| Campo    | Descrição                  |
| -------- | -------------------------- |
| Slave IP | IP do dispositivo escravo. |
| TCP Port | Porta TCP.                 |

Regras de validação:

* O IP precisa ter 4 octetos.
* Cada octeto deve ser menor ou igual a `255`.
* A porta deve estar entre `1` e `65535`.
* Campos ficam desabilitados enquanto há conexão ativa.
* A conexão TCP usa `modbus_new_tcp`.
* O adapter remove zeros à esquerda dos octetos antes de conectar.

Exemplo: o valor configurado como `127.000.000.001` é convertido internamente para `127.0.0.1`.

---

## 4.6 Configurações gerais

A tela de configurações gerais controla opções independentes do modo Modbus.

Campos:

| Campo                       | Descrição                                   |
| --------------------------- | ------------------------------------------- |
| Response Timeout            | Timeout de resposta Modbus.                 |
| Max No Of Bus Monitor Lines | Número máximo de linhas no monitor.         |
| Base Addr                   | Offset/base de endereçamento exibido na UI. |

Regras:

* Algumas opções ficam desabilitadas enquanto há conexão ativa.
* `Base Addr` afeta a forma como o endereço é apresentado e enviado.
* O limite do Bus Monitor controla a quantidade máxima de linhas armazenadas no `RawDataModel`.

---

## 4.7 Conexão e desconexão

A aplicação permite alternar entre os modos:

* **Serial RTU**
* **TCP**

O modo é escolhido na janela principal. O botão `Connect` alterna entre conectar e desconectar.

### Fluxo de conexão RTU

1. Usuário seleciona `Serial RTU`.
2. Usuário configura parâmetros RTU.
3. Usuário informa slave ID.
4. Usuário clica em `Connect`.
5. Aplicação cria contexto RTU.
6. Define slave ID.
7. Configura timeout.
8. Conecta via libmodbus.
9. Atualiza status visual.
10. Habilita ações de leitura/escrita e scan.

### Fluxo de conexão TCP

1. Usuário seleciona `TCP`.
2. Usuário configura IP e porta.
3. Usuário informa slave ID.
4. Usuário clica em `Connect`.
5. Aplicação cria contexto TCP.
6. Remove zeros à esquerda do IP.
7. Define slave ID.
8. Configura timeout.
9. Conecta via libmodbus.
10. Atualiza status visual.
11. Habilita ações de leitura/escrita e scan.

### Fluxo de desconexão

1. Usuário clica novamente em `Connect`.
2. Aplicação para polling, se houver.
3. Fecha o contexto Modbus.
4. Libera o contexto.
5. Atualiza status visual.
6. Desabilita leitura/escrita e scan.

---

## 4.8 Status visual

A barra de status mostra informações de conexão e diagnóstico.

Elementos:

| Indicador        | Descrição                                 |
| ---------------- | ----------------------------------------- |
| Ícone de conexão | Mostra estado conectado/desconectado.     |
| Texto de conexão | Exibe status textual.                     |
| Base Addr        | Mostra base de endereçamento configurada. |
| Packets          | Contador de pacotes/transações.           |
| Errors           | Contador de erros.                        |

O usuário pode resetar os contadores pela ação `Reset Counters`.

---

## 4.9 Seleção de função Modbus

A janela principal permite selecionar as principais funções Modbus de leitura e escrita.

Funções disponíveis:

| Função                   | Código | Tipo                              |
| ------------------------ | -----: | --------------------------------- |
| Read Coils               | `0x01` | Leitura de bits                   |
| Read Discrete Inputs     | `0x02` | Leitura de bits                   |
| Read Holding Registers   | `0x03` | Leitura de registradores 16-bit   |
| Read Input Registers     | `0x04` | Leitura de registradores 16-bit   |
| Write Single Coil        | `0x05` | Escrita de 1 bit                  |
| Write Single Register    | `0x06` | Escrita de 1 registrador          |
| Write Multiple Coils     | `0x0F` | Escrita múltipla de bits          |
| Write Multiple Registers | `0x10` | Escrita múltipla de registradores |

Regras de limite por função:

| Função                   | Quantidade permitida |
| ------------------------ | -------------------: |
| Read Coils               |           até `2000` |
| Read Discrete Inputs     |           até `2000` |
| Read Holding Registers   |            até `125` |
| Read Input Registers     |            até `125` |
| Write Single Coil        |       exatamente `1` |
| Write Single Register    |       exatamente `1` |
| Write Multiple Coils     |         `2` a `2000` |
| Write Multiple Registers |          `2` a `125` |

Quando uma função de escrita simples é selecionada, o campo de quantidade é fixado em `1` e desabilitado. Para funções múltiplas, a quantidade mínima passa a ser `2`.

---

## 4.10 Endereço inicial

O usuário define o endereço inicial pelo campo `Start Address`.

Características:

* Valor máximo: `65535`.
* Pode ser exibido em decimal ou hexadecimal.
* A interface possui seleção separada para a base visual do endereço.
* A aplicação considera também o `Base Addr` configurado nas opções gerais.

O campo `Start Address` é usado para montar a tabela e para enviar a requisição Modbus. Um ponto de atenção: no código analisado, a requisição manual ajusta o endereço subtraindo `baseAddr`, enquanto o ciclo de scan ajusta somando `baseAddr`. Esse comportamento parece inconsistente e deve ser verificado ao recriar a aplicação.

---

## 4.11 Quantidade de coils/registers

O campo `Number of Coils/Registers` define quantos itens serão lidos ou escritos. A aplicação altera automaticamente o texto do label e os limites conforme a função Modbus.

Exemplos:

* Para leitura de coils, o label indica quantidade de coils.
* Para leitura de registers, o label indica quantidade de registers.
* Para escrita de single coil/register, a quantidade é travada em `1`.
* Para escrita múltipla, a quantidade mínima é `2`.

---

## 4.12 Formato dos dados

A aplicação permite visualizar e editar dados em três formatos.

| Formato | Descrição   |
| ------- | ----------- |
| Bin     | Binário     |
| Dec     | Decimal     |
| Hex     | Hexadecimal |

Comportamentos:

* Em `Bin`, a opção `Signed` é escondida.
* Em `Hex`, a opção `Signed` é escondida.
* Em `Dec`, a opção `Signed` é exibida.
* Para dados de 16 bits, o formato binário mostra 16 bits.
* Para coils, o binário é limitado a `0` ou `1`.
* A troca de formato converte os valores já exibidos.

---

## 4.13 Suporte a signed/unsigned

A aplicação possui checkbox `Signed`, disponível principalmente em formato decimal.

Comportamentos:

* Quando `Signed` está ativo, valores de 16 bits podem ser exibidos como signed.
* O limite inferior aceito é `-32768`.
* O limite superior bruto continua relacionado a 16 bits.
* Valores inválidos geram aviso por `InfoBar`.

---

## 4.14 Tabela de registradores/coils

A tabela principal mostra os dados Modbus em forma de grid.

Características:

* Colunas fixas de `00` a `09`.
* Linhas representam blocos de endereços.
* Células fora do range solicitado aparecem como `x`.
* Células dentro do range, mas sem valor válido, aparecem como `-`.
* Valores lidos aparecem formatados conforme base selecionada.
* Células usadas em funções de escrita ficam editáveis.
* Células de leitura não ficam editáveis.
* Tooltips mostram o endereço da célula.
* Cores diferenciam células válidas, inválidas e não utilizadas.

Fluxo de montagem:

1. Usuário altera endereço, quantidade, função ou formato.
2. `MainWindow` chama o adapter para adicionar itens.
3. O adapter chama `RegistersModel::addItems`.
4. O modelo calcula posição inicial baseada em `startAddress % 10`.
5. O modelo cria linhas e colunas.
6. O delegate controla edição dos valores.

---

## 4.15 Leitura manual

A leitura manual ocorre pela ação `Read/Write`, dependendo da função selecionada.

Para funções de leitura:

| Função                 | Chamada libmodbus             |
| ---------------------- | ----------------------------- |
| Read Coils             | `modbus_read_bits`            |
| Read Discrete Inputs   | `modbus_read_input_bits`      |
| Read Holding Registers | `modbus_read_registers`       |
| Read Input Registers   | `modbus_read_input_registers` |

Fluxo:

1. Usuário escolhe função de leitura.
2. Define slave ID, start address e quantidade.
3. Clica em `Read/Write`.
4. A aplicação valida se a tabela possui itens.
5. O adapter executa a função libmodbus correspondente.
6. Se o retorno for igual à quantidade esperada, os dados são inseridos na tabela.
7. Se houver erro, a aplicação:

   * marca valores como inválidos;
   * incrementa contador de erro;
   * registra erro no monitor/log;
   * exibe mensagem pela `InfoBar`;
   * executa flush no contexto Modbus.

---

## 4.16 Escrita manual

Para funções de escrita, a ação `Read/Write` envia valores da tabela para o escravo.

Funções:

| Função                   | Chamada libmodbus        |
| ------------------------ | ------------------------ |
| Write Single Coil        | `modbus_write_bit`       |
| Write Single Register    | `modbus_write_register`  |
| Write Multiple Coils     | `modbus_write_bits`      |
| Write Multiple Registers | `modbus_write_registers` |

Fluxo:

1. Usuário escolhe função de escrita.
2. A tabela é criada com células editáveis.
3. Usuário preenche valores.
4. Clica em `Read/Write`.
5. O adapter lê os valores do `RegistersModel`.
6. O adapter executa a chamada libmodbus correspondente.
7. Em sucesso, registra mensagem de sucesso.
8. Em erro, incrementa contador, registra erro, exibe `InfoBar` e executa flush.

Comportamento adicional: quando uma função de escrita é selecionada e já existe conexão ativa, o adapter tenta pré-ler os valores atuais usando leitura de coils ou holding registers para preencher a tabela antes da edição.

---

## 4.17 Scan / polling periódico

A funcionalidade `Scan` executa requisições repetidas em intervalo definido pelo usuário.

Campos relacionados:

| Campo                     | Descrição                   |
| ------------------------- | --------------------------- |
| Scan Rate                 | Intervalo em milissegundos. |
| Function Code             | Função executada no ciclo.  |
| Start Address             | Endereço inicial.           |
| Number of Coils/Registers | Quantidade lida/escrita.    |

Regras:

* O scan usa um `QTimer`.
* Durante o scan, vários controles da interface são desabilitados.
* A aplicação exige que o intervalo de scan seja maior ou igual ao dobro do timeout configurado.
* O scan pode ser iniciado e parado pelo mesmo botão.
* Cada ciclo chama a mesma rotina central de transação Modbus.
* Contadores de pacotes e erros são atualizados normalmente.

Fluxo:

1. Usuário configura função, endereço, quantidade e intervalo.
2. Clica em `Scan`.
3. Aplicação valida tabela e intervalo.
4. Configura parâmetros no adapter.
5. Inicia timer.
6. A cada timeout do timer, executa `modbusTransaction`.
7. Ao parar, timer é interrompido e a UI é reabilitada.

---

## 4.18 Bus Monitor

O Bus Monitor é uma das funcionalidades principais para diagnóstico. Ele permite observar o tráfego Modbus bruto e interpretar mensagens.

Funcionalidades detalhadas:

### Visualização

* Lista de mensagens brutas.
* Área textual com interpretação da ADU/PDU.
* Toolbar com ações `Save`, `Clear` e `Exit`.

### Tipos de linha

| Prefixo | Significado          |
| ------- | -------------------- |
| `Sys`   | Mensagem de sistema  |
| `Tx`    | Mensagem transmitida |
| `Rx`    | Mensagem recebida    |

### Interpretação de Tx

Para mensagens transmitidas, o monitor mostra:

* Tipo da mensagem.
* Timestamp.
* Modo Modbus.
* Slave Address ou Unit ID.
* Function Code.
* Starting Address.
* Quantity of Outputs.
* Output Value.
* Byte Count.
* Output Values, quando aplicável.

### Interpretação de Rx

Para mensagens recebidas, o monitor mostra:

* Tipo da mensagem.
* Timestamp.
* Modo Modbus.
* Slave Address ou Unit ID.
* Function Code.
* Byte Count.
* Valores retornados.
* Starting Address em respostas de escrita.
* Quantity of Outputs em escrita múltipla.
* Exception Code quando a resposta é exceção Modbus.

### RTU

Em modo RTU, o monitor interpreta:

* Slave Address.
* PDU.
* CRC.

### TCP

Em modo TCP, o monitor interpreta:

* Transaction ID.
* Protocol ID.
* Length.
* Unit ID.
* PDU.

### Exportação

O usuário pode salvar as linhas do monitor em arquivo texto.

---

## 4.19 Ferramentas auxiliares

A janela `Tools` fornece diagnósticos rápidos.

### Report Slave ID

Executa diagnóstico Modbus usando `modbus_report_slave_id`.

Saída esperada:

* Run Status.
* Slave ID ou Server ID.
* Mensagem de erro, se houver.

### Ping

Disponível em modo TCP.

Fluxo:

1. Usa o IP configurado.
2. Remove zeros à esquerda.
3. Executa comando de sistema `ping`.
4. Aguarda até 5 segundos.
5. Mostra saída padrão e erro padrão.

### Port Status

Disponível em modo TCP.

Fluxo:

1. Usa IP e porta configurados.
2. Abre conexão via `QTcpSocket`.
3. Aguarda até 5 segundos.
4. Informa se a porta está aberta ou fechada.

---

## 4.20 Sessões

A aplicação permite salvar e carregar sessões.

Uma sessão guarda o estado operacional da interface, incluindo:

* Modo Modbus.
* Slave ID.
* Scan rate.
* Function code.
* Start address.
* Number of coils/registers.
* Formato de dados.

Funcionalidades:

| Ação         | Descrição                                      |
| ------------ | ---------------------------------------------- |
| Load Session | Carrega arquivo `.ses` e atualiza a interface. |
| Save Session | Salva estado atual em arquivo `.ses`.          |

Isso permite que o usuário retome rapidamente uma configuração de teste Modbus.

---

## 4.21 Limpeza da tabela

A ação `Clear Table` limpa/recria os itens exibidos na tabela de registradores/coils.

Comportamento esperado:

* Remove valores antigos.
* Recria a grade conforme função, endereço e quantidade atuais.
* Mantém coerência com formato selecionado.
* Prepara a tabela para nova leitura ou escrita.

---

## 4.22 Reset de contadores

A ação `Reset Counters` zera os contadores de pacotes e erros.

Contadores:

* `Packets`
* `Errors`

Esses valores são exibidos na barra de status.

---

## 4.23 Abertura de log

A aplicação possui ação para abrir o arquivo de log.

Arquivo padrão:

* `QModMaster.log`

Uso:

* Diagnóstico de falhas.
* Verificação de erros de comunicação.
* Auditoria de eventos da aplicação.

---

## 4.24 Manual Modbus

A interface possui ação `Modbus Manual`.

Função esperada:

* Abrir documentação/manual incluído no projeto.
* Servir como referência rápida para códigos, conceitos e estrutura Modbus.

---

## 4.25 About

A aplicação possui janela `About`.

Função:

* Exibir informações sobre a aplicação.
* Exibir créditos ou versão.
* Acesso pelo menu `Help`.

---

## 4.26 Exibição/ocultação de headers

Existe ação `Headers` na interface.

Função provável, pelo contexto da tabela:

* Alternar exibição de cabeçalhos da tabela de registradores/coils.
* Melhorar visualização dependendo do tamanho da tabela.

---

# 5. Regras de negócio e validações

## 5.1 Limites Modbus

A aplicação respeita limites típicos de quantidade por operação:

| Tipo                     |       Limite |
| ------------------------ | -----------: |
| Coils/discrete inputs    |       `2000` |
| Registers                |        `125` |
| Single write             |          `1` |
| Multiple write coils     | `2` a `2000` |
| Multiple write registers |  `2` a `125` |

Esses limites são aplicados dinamicamente quando o usuário troca a função Modbus.

---

## 5.2 Validação de IP

A configuração TCP valida:

* Quatro octetos.
* Cada octeto até `255`.
* Porta de `1` até `65535`.

Em erro, exibe mensagem indicando IP ou porta incorreta.

---

## 5.3 Validação de scan rate

A aplicação impede scan com intervalo menor que o dobro do timeout configurado.

Regra:

`scanRate >= timeout * 1000 * 2`

Motivo: evitar que uma nova transação seja iniciada antes que a anterior tenha tempo suficiente para responder ou expirar.

---

## 5.4 Validação de valores editados

Na tabela:

* Coil aceita apenas `0` ou `1`.
* Register aceita valores compatíveis com 16 bits.
* Decimal signed aceita valores negativos até `-32768`.
* Valores acima de `65535` são recusados.
* Hexadecimal usa máscara de 4 dígitos.
* Binário register usa máscara de 16 bits.

---

## 5.5 Estado da interface

A interface muda conforme estado de conexão e scan.

Quando desconectado:

* `Read/Write` fica desabilitado.
* `Scan` fica desabilitado.
* Configurações ficam editáveis.

Quando conectado:

* `Read/Write` fica habilitado.
* `Scan` fica habilitado.
* Algumas configurações de comunicação ficam bloqueadas.

Durante scan:

* Controles que alterariam a transação ficam desabilitados.
* O botão de scan vira ação de parada.
* O timer executa transações automaticamente.

---

# 6. Modelo de dados para recriação

## 6.1 Configuração principal

Um clone funcional pode modelar a configuração assim:

```text
AppConfig
- tcpPort: int
- slaveIp: string
- serialDevice: string
- serialPort: int
- baud: int
- dataBits: int
- stopBits: string
- parity: string
- rts: string
- maxBusMonitorLines: int
- baseAddress: int
- timeoutSeconds: int
- loggingLevel: int
```

## 6.2 Sessão

```text
SessionConfig
- modbusMode: RTU | TCP
- slaveId: int
- scanRateMs: int
- functionCode: int
- startAddress: int
- quantity: int
- displayBase: Bin | Dec | Hex
```

## 6.3 Célula da tabela

```text
RegisterCell
- address: int
- value: int | null
- visibleText: string
- isUsed: bool
- isEditable: bool
- isValid: bool
- tooltip: string
```

## 6.4 Mensagem bruta do monitor

```text
RawMessage
- direction: Sys | Tx | Rx
- timestamp: datetime
- mode: RTU | TCP
- rawHex: string
- parsedFields: map<string, string>
```

## 6.5 Estado de comunicação

```text
ModbusState
- connected: bool
- mode: None | RTU | TCP
- packets: int
- errors: int
- currentSlaveId: int
- currentFunctionCode: int
- currentStartAddress: int
- currentQuantity: int
- pollingEnabled: bool
```

---

# 7. Fluxos principais para recriação

## 7.1 Fluxo de inicialização

```text
Start
→ Load translations
→ Configure logger
→ Load qModMaster.ini
→ Create ModbusAdapter
→ Create MainWindow
→ Bind UI events
→ Bind adapter signals
→ Render initial table
→ Show window
```

---

## 7.2 Fluxo de conexão

```text
User clicks Connect
→ Read selected mode
→ If RTU:
    read serial settings
    create RTU context
→ If TCP:
    read IP/port
    normalize IP
    create TCP context
→ Set slave ID
→ Set timeout
→ Enable error recovery
→ Connect
→ If success:
    update status as connected
    enable Read/Write and Scan
→ If error:
    show InfoBar
    log error
```

---

## 7.3 Fluxo de leitura

```text
User clicks Read/Write
→ Validate table exists
→ Read function code
→ Read start address
→ Read quantity
→ Increment packet counter
→ Execute libmodbus read function
→ If success:
    update RegistersModel
    refresh table
→ If error:
    increment error counter
    mark values invalid
    add raw error line
    show InfoBar
    flush Modbus context
```

---

## 7.4 Fluxo de escrita

```text
User edits table
→ User clicks Read/Write
→ Validate table exists
→ Collect values from RegistersModel
→ Select write function
→ Increment packet counter
→ Execute libmodbus write function
→ If success:
    log success
    optionally refresh table
→ If error:
    increment error counter
    show InfoBar
    add raw error line
    flush Modbus context
```

---

## 7.5 Fluxo de scan

```text
User clicks Scan
→ Validate table
→ Validate scanRate >= timeout * 2
→ Store current transaction parameters
→ Disable editing controls
→ Start QTimer
→ On every timer tick:
    execute modbusTransaction
→ User clicks Scan again:
    stop QTimer
    re-enable controls
```

---

## 7.6 Fluxo do Bus Monitor

```text
User opens Bus Monitor
→ Enable raw line capture
→ Display RawDataModel
→ User selects raw line
→ Detect prefix Sys/Tx/Rx
→ Parse RTU or TCP ADU
→ Parse PDU
→ Show interpreted fields
→ On close:
    disable capture
    clear monitor
```

---

## 7.7 Fluxo das ferramentas

```text
User opens Tools
→ Select mode/tool
→ If Report Slave ID:
    execute modbus_report_slave_id
→ If Ping:
    run system ping against configured TCP IP
→ If Port Status:
    open QTcpSocket to configured IP/port
→ Show result text
```

---

# 8. Especificação de telas

## 8.1 Janela principal

Elementos obrigatórios:

* Combo `Modbus Mode`: `Serial RTU`, `TCP`.
* Campo `Slave Addr`.
* Campo `Scan Rate`, em ms.
* Combo `Function Code`.
* Campo `Start Address`.
* Combo da base do endereço: decimal/hexadecimal.
* Campo `Number of Coils/Registers`.
* Combo `Data Format`: `Bin`, `Dec`, `Hex`.
* Checkbox `Signed`.
* Tabela de registradores/coils.
* Toolbar com ações principais.
* Menu `File`.
* Menu `Options`.
* Menu `View`.
* Menu `Commands`.
* Menu `Help`.
* Barra de status com conexão, base address, pacotes e erros.

---

## 8.2 Tela de configuração RTU

Campos:

* Serial device.
* Serial port.
* Baud.
* Data bits.
* Stop bits.
* Parity.
* RTS.
* OK.
* Cancel.

---

## 8.3 Tela de configuração TCP

Campos:

* Slave IP.
* TCP Port.
* OK.
* Cancel.

---

## 8.4 Tela de configurações gerais

Campos:

* Response Timeout.
* Max No Of Bus Monitor Lines.
* Base Addr.
* OK.
* Cancel.

---

## 8.5 Bus Monitor

Elementos:

* Lista de mensagens brutas.
* Painel de interpretação da ADU/PDU.
* Toolbar com `Save`, `Clear` e `Exit`.

---

## 8.6 Tools

Elementos:

* Combo de modo.
* Combo de comando.
* Área textual de resultado.
* Toolbar com `Exec`, `Clear` e `Exit`.

---

# 9. Detalhes de implementação importantes

## 9.1 Uso da libmodbus

A aplicação usa diretamente chamadas da libmodbus para cada operação.

Mapeamento principal:

```text
Read Coils                → modbus_read_bits
Read Discrete Inputs      → modbus_read_input_bits
Read Holding Registers    → modbus_read_registers
Read Input Registers      → modbus_read_input_registers
Write Single Coil         → modbus_write_bit
Write Single Register     → modbus_write_register
Write Multiple Coils      → modbus_write_bits
Write Multiple Registers  → modbus_write_registers
Report Slave ID           → modbus_report_slave_id
```

---

## 9.2 Captura Tx/Rx

O projeto usa callbacks integrados à camada Modbus para capturar mensagens transmitidas e recebidas, formatando bytes em hexadecimal e adicionando linhas ao `RawDataModel`.

Formato conceitual da linha:

```text
<Tipo> <Timestamp> <Modo> <bytes em hexadecimal>
```

Exemplos de tipo:

```text
Sys
Tx
Rx
```

---

## 9.3 Tratamento de erros

Em caso de erro Modbus, a aplicação:

1. Incrementa contador de erros.
2. Marca valores como inválidos.
3. Registra erro no RawDataModel.
4. Exibe aviso por InfoBar.
5. Faz flush do contexto Modbus.
6. Atualiza a UI.

Erros comuns são traduzidos por `EUtils::libmodbus_strerror`, incluindo casos como timeout, conexão recusada, conexão resetada, pipe quebrado e argumento inválido.

---

## 9.4 Pré-leitura em funções de escrita

Quando o usuário seleciona uma função de escrita e a aplicação está conectada, o adapter tenta buscar o valor atual antes de permitir a edição.

Comportamento:

* Para escrita de coils, pré-lê usando `Read Coils`.
* Para escrita de registers, pré-lê usando `Read Holding Registers`.
* Depois preenche a tabela com os valores atuais.
* Isso ajuda o usuário a alterar valores existentes em vez de começar com células vazias.

---

## 9.5 Conversão de valores

A conversão de valores precisa considerar:

* Base visual atual.
* Se o dado é de 1 bit ou 16 bits.
* Se decimal signed está ativo.
* Se o valor excede o range.
* Se a célula representa dado real ou preenchimento visual.

Regras de renderização:

| Caso                             | Exibição                            |
| -------------------------------- | ----------------------------------- |
| Célula fora do range             | `x`                                 |
| Célula dentro do range sem valor | `-`                                 |
| Valor inválido após erro         | `-/-`                               |
| Coil em binário                  | `0` ou `1`                          |
| Register em binário              | 16 bits                             |
| Register em hex                  | 4 dígitos hex                       |
| Register decimal signed          | valor convertido para signed 16-bit |

---

# 10. Pontos de atenção para recriação

## 10.1 Possível inconsistência no cálculo de endereço

Ao executar uma requisição manual, o código ajusta o endereço inicial subtraindo `baseAddr`. No scan, o código aparenta ajustar somando `baseAddr`. Isso deve ser validado porque pode causar diferença entre o comportamento de leitura manual e polling automático.

Ao recriar o projeto, há duas opções:

1. **Reproduzir exatamente o comportamento original**, inclusive a inconsistência.
2. **Corrigir o comportamento**, usando a mesma regra para leitura manual e scan.

A escolha deve ser documentada porque impacta compatibilidade com sessões e expectativas de usuários antigos.

---

## 10.2 Timeout padrão

O valor padrão de timeout aparece como `0` nas configurações carregadas. Como o scan valida o intervalo com base no timeout, uma recriação deve decidir se mantém esse padrão ou define um timeout mínimo mais seguro.

---

## 10.3 Dependências embutidas

A libmodbus e o QsLog estão incluídos no projeto. Uma recriação moderna poderia:

* Manter as dependências vendorizadas.
* Usar submodules Git.
* Usar package manager C++.
* Migrar para CMake.
* Usar libmodbus instalada no sistema.

Para compatibilidade histórica, o caminho mais fiel é manter as dependências junto ao projeto, como no `.pro` original.

---

## 10.4 UI baseada em Qt Designer

A interface é fortemente baseada em arquivos `.ui`. Uma recriação fiel deve preservar a separação entre:

* Layout visual em `.ui`.
* Lógica em classes C++.
* Modelos Qt para tabelas/listas.
* Delegates para edição especializada.

---

# 11. Critérios de aceitação para uma recriação

Uma recriação pode ser considerada funcionalmente equivalente quando atender aos seguintes critérios.

## 11.1 Comunicação

* Conecta via Modbus RTU.
* Conecta via Modbus TCP.
* Desconecta corretamente.
* Permite configurar slave ID.
* Permite configurar IP e porta TCP.
* Permite configurar parâmetros seriais RTU.
* Configura timeout.
* Trata erros de conexão.

## 11.2 Funções Modbus

* Executa `Read Coils`.
* Executa `Read Discrete Inputs`.
* Executa `Read Holding Registers`.
* Executa `Read Input Registers`.
* Executa `Write Single Coil`.
* Executa `Write Single Register`.
* Executa `Write Multiple Coils`.
* Executa `Write Multiple Registers`.
* Executa `Report Slave ID` pela tela Tools.

## 11.3 Interface principal

* Exibe modo Modbus.
* Exibe slave ID.
* Exibe scan rate.
* Exibe function code.
* Exibe start address.
* Exibe quantidade.
* Exibe formato Bin/Dec/Hex.
* Exibe checkbox Signed quando aplicável.
* Exibe tabela de dados.
* Exibe barra de status.
* Habilita/desabilita ações conforme conexão.
* Habilita/desabilita ações durante scan.

## 11.4 Tabela

* Cria células alinhadas por endereço.
* Mostra colunas `00` a `09`.
* Marca células fora de range.
* Permite edição apenas para escrita.
* Converte valores entre bases.
* Valida limites.
* Exibe tooltips com endereço.

## 11.5 Scan

* Executa polling periódico.
* Usa timer.
* Respeita scan rate.
* Bloqueia alterações durante scan.
* Para scan corretamente.
* Atualiza contadores.

## 11.6 Bus Monitor

* Captura Tx.
* Captura Rx.
* Captura Sys.
* Interpreta RTU.
* Interpreta TCP.
* Mostra ADU/PDU.
* Salva log bruto.
* Limpa log.
* Respeita limite máximo de linhas.

## 11.7 Persistência

* Carrega `qModMaster.ini`.
* Salva `qModMaster.ini`.
* Carrega sessão `.ses`.
* Salva sessão `.ses`.
* Restaura últimos parâmetros operacionais.

## 11.8 Diagnóstico

* Abre arquivo de log.
* Registra erros.
* Exibe mensagens amigáveis.
* Conta pacotes.
* Conta erros.
* Reseta contadores.
* Executa ping TCP.
* Verifica porta TCP.

---

# 12. Plano recomendado para recriação

## Etapa 1: Base do projeto

* Criar projeto Qt Widgets.
* Criar `MainWindow`.
* Adicionar `.ui` para janela principal.
* Criar estrutura de pastas equivalente.
* Integrar libmodbus.
* Integrar logging.

## Etapa 2: Configurações

* Implementar `AppConfig`.
* Persistir em `QSettings`.
* Criar telas RTU, TCP e Settings.
* Implementar validações.

## Etapa 3: Comunicação

* Implementar `ModbusAdapter`.
* Criar conexão RTU.
* Criar conexão TCP.
* Implementar disconnect.
* Implementar timeout.
* Implementar contadores.

## Etapa 4: Operações Modbus

* Implementar leitura de coils.
* Implementar leitura de discrete inputs.
* Implementar leitura de holding registers.
* Implementar leitura de input registers.
* Implementar escrita single.
* Implementar escrita múltipla.
* Implementar tratamento de erro.

## Etapa 5: Tabela

* Criar `RegistersModel`.
* Criar `RegistersDataDelegate`.
* Implementar renderização por endereço.
* Implementar edição.
* Implementar conversão Bin/Dec/Hex.
* Implementar signed/unsigned.

## Etapa 6: Scan

* Adicionar `QTimer`.
* Implementar start/stop.
* Bloquear UI durante scan.
* Validar intervalo.
* Reutilizar rotina de transação.

## Etapa 7: Bus Monitor

* Criar `RawDataModel`.
* Capturar Tx/Rx.
* Criar janela Bus Monitor.
* Implementar parser RTU.
* Implementar parser TCP.
* Implementar exportação.

## Etapa 8: Tools

* Criar janela Tools.
* Implementar Report Slave ID.
* Implementar Ping.
* Implementar Port Status.

## Etapa 9: Polimento

* Adicionar ícones.
* Adicionar traduções.
* Adicionar About.
* Adicionar abertura do log.
* Adicionar manual.
* Testar Windows/Linux/macOS.

---

# 13. Resumo final

O qModMaster é um mestre Modbus gráfico completo, com suporte a RTU e TCP, leitura e escrita das principais áreas Modbus, tabela editável de coils/registers, polling periódico, monitor bruto de barramento, persistência de configuração/sessões, logging, ferramentas de diagnóstico e suporte a tradução.

Para recriá-lo, o núcleo deve ser dividido em quatro camadas:

1. **Interface Qt**: menus, toolbar, dialogs e tabela.
2. **Modelo de dados**: registradores/coils, mensagens brutas e configurações.
3. **Comunicação Modbus**: adapter baseado em libmodbus.
4. **Diagnóstico**: logs, Bus Monitor, contadores e ferramentas.

A parte mais importante para fidelidade funcional é reproduzir a interação entre `MainWindow`, `ModbusAdapter`, `RegistersModel`, `RawDataModel` e `ModbusCommSettings`, pois esses componentes formam o fluxo completo da aplicação: configurar, conectar, montar tabela, executar transação, exibir dados, monitorar tráfego e persistir estado.
