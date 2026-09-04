# 🤖 RPA - Automação de Relatórios

Projeto responsável pela automação de processos relacionados à extração, tratamento,
atualização e disponibilização de dados operacionais do PrimeBuilder.

O projeto reúne diferentes RPAs utilizados para automatizar rotinas de relatórios,
visitas de supervisores, visitas de gerentes e processamento de MPS.

---

## 📌 Objetivo

Automatizar processos que anteriormente dependiam de execução manual, reduzindo
tempo operacional, erros de digitação e inconsistências nos dados.

Os robôs realizam atividades como:

- Acesso automatizado ao PrimeBuilder;
- Extração e exportação de relatórios;
- Tratamento e padronização de dados;
- Integração com APIs;
- Atualização de bases no Google Sheets;
- Controle de registros duplicados;
- Atualização de registros existentes;
- Geração de informações para dashboards;
- Execução automática por agendamento;
- Registro de logs para acompanhamento das execuções.

---

## 📂 Estrutura do Projeto

```text
RPA_RELATORIOS/
│
├── RPA_PRIMEBUILDER_MPS_V1/
│   ├── main.py
│   ├── requirements.txt
│   └── ...
│
├── RPA_PRIMEBUILDER_Visitas_Gerentes/
│   ├── main.py
│   ├── requirements.txt
│   └── ...
│
├── RPA_PRIMEBUILDER_Visitas_Supervisores/
│   ├── main.py
│   ├── requirements.txt
│   └── ...
│
├── .gitignore
└── README.md
