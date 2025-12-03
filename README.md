O script faz a raspagem de todos os mandados de prisão de todos os Estado do Banco Nacional de Mandados de Prisão.

O arquivo é salvo em formato .csv

Na execução do Script é necessário informar o cookie de sessão do portal BNMP, conforme instruções a seguir:
1. Acessar o site: https://portalbnmp.cnj.jus.br/.
2. Validar o Captcha
   <img width="1329" height="287" alt="image" src="https://github.com/user-attachments/assets/339a394e-9e5a-447d-a41b-6caab59ab146" />
3. Abrir as ferramentas de desenvolvedor (F12)
4. Abrir a opção: Application ou Aplicação
5. Abrir a opção Cookies/https://portalbnmp.cnj.jus.br/
6. Procurar pelo nome: portalbnmp
7. Copiar o cookie de sessão, exemplo: eyJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJndWVzdF9wb3J0YWxibm1wXzE3NjQ3OTc2NTQ5NTciLCJhdXRoIjoiUk9MRV9BTk9OWU1PVVMiLCJleHAiOjE3NjQ3OTc5NTR9.4ffRcB19Qfgwk0IDQYvJsD5DegnUzcDbE9j04qCbKRfKhaBDg-TUVA6iD1xS7RGkbwmsdXfwTMuDmSNerp9YQQ
8. Colar todo o conteúdo no script, quando requisitado

<img width="550" height="620" alt="image" src="https://github.com/user-attachments/assets/291955f6-54d5-447e-aa59-98c899a15e1d" />

Atenção -> O site do BNMP revalida o cookie de sessão a todo o momento, é recomendável que a cada extração seja atualizado o reCAPTCHA e informado um novo Cookie.
