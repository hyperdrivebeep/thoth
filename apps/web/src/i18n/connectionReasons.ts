import { pickLocale } from "./pickLocale";
const korean = {
  CODEX_STANDALONE_NOT_INSTALLED: "ChatGPT 로그인에 필요한 Codex 연결 도구가 이 PC에 없습니다.",
  CODEX_STANDALONE_PIN_UNAVAILABLE:
    "ChatGPT 로그인에 필요한 Codex 연결 도구(0.157.1)를 찾지 못했습니다.",
  CODEX_PACKAGE_INTEGRITY_UNAVAILABLE:
    "Codex 연결 도구의 설치 정보를 확인하지 못했습니다. 다시 설치하세요.",
  CODEX_PACKAGE_INTEGRITY_MISMATCH:
    "Codex 연결 도구 파일이 공식 배포본과 다릅니다. 다시 설치하세요.",
  CODEX_PACKAGE_ROOT_ABSOLUTE_REQUIRED: "Codex 연결 도구 위치 설정이 올바르지 않습니다.",
  UPDATE_REVIEW_REQUIRED: "Codex 연결 도구의 위치나 버전이 바뀌었습니다. 다시 설치하세요.",
  CODEX_PINNED_PLATFORM_UNSUPPORTED:
    "이 운영체제에서는 ChatGPT 로그인을 아직 지원하지 않습니다. API 키로 연결하세요.",
  CLAUDE_CLIENT_REGISTRATION_REQUIRED:
    "Claude 구독 로그인은 이 PC에서 아직 켜지지 않았습니다. 지금은 API 키로 연결할 수 있습니다.",
  CLAUDE_CODE_NOT_INSTALLED: "Claude 로그인에 필요한 Claude Code가 이 PC에 없습니다.",
  CLAUDE_CODE_NATIVE_BINARY_REQUIRED:
    "Claude Code 실행 파일을 찾지 못했습니다. THOTH 전용 폴더에 설치하세요.",
  CLAUDE_CODE_NOT_EXECUTABLE: "Claude Code 실행 파일을 실행할 수 없습니다. 다시 설치하세요.",
  CLAUDE_CODE_CAPABILITY_UNSUPPORTED:
    "설치된 Claude Code 버전이 너무 오래됐습니다. THOTH 전용 폴더에 새로 설치하세요.",
  CLAUDE_CODE_LOGIN_START_FAILED:
    "Claude Code가 로그인을 시작하지 못했습니다. 잠시 뒤 다시 시도하세요.",
  CLAUDE_CODE_LOGIN_REQUIRED:
    "Claude 로그인이 필요합니다. 설정의 모델 연결에서 'Claude로 로그인'을 눌러 로그인한 뒤 다시 실행하세요.",
  CLAUDE_CODE_LOGIN_FAILED:
    "Claude 로그인이 끝나지 않았습니다. 새 로그인은 직접 선택할 때만 시작됩니다.",
  CLAUDE_CODE_LOGIN_NOT_CONFIRMED:
    "Claude Code가 종료됐지만 로그인 상태를 확인하지 못했습니다. 다시 시도하세요.",
  LOGIN_REQUIRED: "로그인이 필요합니다.",
  LOGIN_PENDING: "로그인을 마무리하는 중입니다.",
  XAI_LOGIN_REQUIRED: "xAI 로그인이 필요합니다.",
  MODEL_CREDENTIAL_UNAVAILABLE: "등록된 API 키가 없습니다.",
  MODEL_CREDENTIAL_STORE_UNREADABLE:
    "키 파일을 읽을 수 없어 새 키를 저장하지 않았습니다. 작업 공간의 model-registry 폴더에 있는 secrets.json과 index.json을 백업에서 되돌리거나 다른 곳으로 옮긴 뒤 다시 등록하세요.",
  MODEL_CREDENTIAL_STORE_UNWRITABLE:
    "키 파일을 저장하지 못했습니다. 이전에 등록한 키는 그대로입니다. 디스크 공간과 작업 공간 폴더의 쓰기 권한을 확인하세요.",
  NODE_NPM_UNAVAILABLE:
    "자동 설치에는 Node.js(npm)가 필요합니다. Node.js LTS를 설치한 뒤 다시 시도하거나 아래 명령을 직접 실행하세요.",
  CODEX_TOOL_INSTALL_FAILED:
    "Codex 도구 설치 명령이 실패했습니다. 인터넷 연결을 확인하고 다시 시도하세요.",
  CLAUDE_CODE_TOOL_INSTALL_FAILED:
    "Claude Code 설치 명령이 실패했습니다. 인터넷 연결을 확인하고 다시 시도하세요.",
  CODEX_TOOL_INSTALL_TIMEOUT: "설치가 5분 안에 끝나지 않았습니다. 잠시 뒤 다시 시도하세요.",
  CLAUDE_CODE_TOOL_INSTALL_TIMEOUT: "설치가 5분 안에 끝나지 않았습니다. 잠시 뒤 다시 시도하세요.",
  MODEL_TOOL_INSTALL_TIMEOUT: "설치가 제때 끝나지 않았습니다. 잠시 뒤 상태를 다시 확인하세요.",
  MODEL_TOOL_INSTALL_IN_PROGRESS: "이 도구를 이미 설치하는 중입니다. 끝날 때까지 기다리세요.",
  MODEL_TOOL_PREFIX_UNAVAILABLE:
    "THOTH 도구 폴더(LOCALAPPDATA)를 확인하지 못했습니다. 아래 명령을 직접 실행하세요.",
  CATALOG_UNAVAILABLE: "로그인은 됐지만 모델 목록을 아직 불러오지 못했습니다.",
  MODEL_CATALOG_REFRESH_TIMEOUT:
    "모델 목록 확인이 제때 끝나지 않았습니다. 잠시 뒤 다시 시도하세요.",
} as const;
const english: Record<keyof typeof korean, string> = {
  CODEX_STANDALONE_NOT_INSTALLED:
    "The Codex connection tool required for ChatGPT login is not installed on this computer.",
  CODEX_STANDALONE_PIN_UNAVAILABLE:
    "The required Codex connection tool version (0.157.1) was not found.",
  CODEX_PACKAGE_INTEGRITY_UNAVAILABLE:
    "The Codex installation could not be verified. Reinstall the tool.",
  CODEX_PACKAGE_INTEGRITY_MISMATCH:
    "Codex tool files differ from the official distribution. Reinstall the tool.",
  CODEX_PACKAGE_ROOT_ABSOLUTE_REQUIRED: "The configured Codex tool location is invalid.",
  UPDATE_REVIEW_REQUIRED: "The Codex tool location or version changed. Reinstall the tool.",
  CODEX_PINNED_PLATFORM_UNSUPPORTED:
    "ChatGPT login is not yet supported on this operating system. Connect with an API key.",
  CLAUDE_CLIENT_REGISTRATION_REQUIRED:
    "Claude subscription login is not enabled on this computer. You can currently connect with an API key.",
  CLAUDE_CODE_NOT_INSTALLED:
    "Claude Code, required for Claude login, is not installed on this computer.",
  CLAUDE_CODE_NATIVE_BINARY_REQUIRED:
    "The Claude Code executable was not found. Install it in the THOTH tools folder.",
  CLAUDE_CODE_NOT_EXECUTABLE: "The Claude Code executable could not run. Reinstall it.",
  CLAUDE_CODE_CAPABILITY_UNSUPPORTED:
    "The installed Claude Code version is too old. Install the supported version in the THOTH tools folder.",
  CLAUDE_CODE_LOGIN_START_FAILED: "Claude Code could not start login. Try again shortly.",
  CLAUDE_CODE_LOGIN_REQUIRED:
    "Sign in to Claude using model connections in Settings, then try again.",
  CLAUDE_CODE_LOGIN_FAILED:
    "Claude login did not finish. A new login starts only when you choose it.",
  CLAUDE_CODE_LOGIN_NOT_CONFIRMED:
    "Claude Code exited, but login could not be confirmed. Try again.",
  LOGIN_REQUIRED: "Login required.",
  LOGIN_PENDING: "Finishing login.",
  XAI_LOGIN_REQUIRED: "xAI login required.",
  MODEL_CREDENTIAL_UNAVAILABLE: "No API key is registered.",
  MODEL_CREDENTIAL_STORE_UNREADABLE:
    "The key file could not be read, so the new key was not saved. Restore secrets.json and index.json in the workspace's model-registry folder from a backup, or move them away, then register again.",
  MODEL_CREDENTIAL_STORE_UNWRITABLE:
    "The key file could not be saved. The keys registered before are unchanged. Check disk space and write permission on the workspace folder.",
  NODE_NPM_UNAVAILABLE:
    "Automatic installation needs Node.js (npm). Install Node.js LTS and try again, or run the command below yourself.",
  CODEX_TOOL_INSTALL_FAILED:
    "Codex tool installation failed. Check your internet connection and try again.",
  CLAUDE_CODE_TOOL_INSTALL_FAILED:
    "Claude Code installation failed. Check your internet connection and try again.",
  CODEX_TOOL_INSTALL_TIMEOUT: "Installation did not finish within five minutes. Try again shortly.",
  CLAUDE_CODE_TOOL_INSTALL_TIMEOUT:
    "Installation did not finish within five minutes. Try again shortly.",
  MODEL_TOOL_INSTALL_TIMEOUT: "Installation did not finish in time. Check its status shortly.",
  MODEL_TOOL_INSTALL_IN_PROGRESS: "This tool is already being installed. Wait for it to finish.",
  MODEL_TOOL_PREFIX_UNAVAILABLE:
    "The THOTH tools folder (LOCALAPPDATA) could not be located. Run the command below yourself.",
  CATALOG_UNAVAILABLE: "Login succeeded, but the model list is not available yet.",
  MODEL_CATALOG_REFRESH_TIMEOUT: "The model list check did not finish in time. Try again shortly.",
};
const chinese: Record<keyof typeof korean, string> = {
  CODEX_STANDALONE_NOT_INSTALLED: "这台电脑上没有 ChatGPT 登录所需的 Codex 连接工具。",
  CODEX_STANDALONE_PIN_UNAVAILABLE: "未找到所需版本（0.157.1）的 Codex 连接工具。",
  CODEX_PACKAGE_INTEGRITY_UNAVAILABLE: "无法验证 Codex 的安装信息。请重新安装该工具。",
  CODEX_PACKAGE_INTEGRITY_MISMATCH: "Codex 工具文件与官方发行版不一致。请重新安装该工具。",
  CODEX_PACKAGE_ROOT_ABSOLUTE_REQUIRED: "Codex 工具的位置设置无效。",
  UPDATE_REVIEW_REQUIRED: "Codex 工具的位置或版本已变化。请重新安装该工具。",
  CODEX_PINNED_PLATFORM_UNSUPPORTED: "此操作系统暂不支持 ChatGPT 登录。请使用 API 密钥连接。",
  CLAUDE_CLIENT_REGISTRATION_REQUIRED:
    "这台电脑上尚未启用 Claude 订阅登录。目前可以使用 API 密钥连接。",
  CLAUDE_CODE_NOT_INSTALLED: "这台电脑上没有 Claude 登录所需的 Claude Code。",
  CLAUDE_CODE_NATIVE_BINARY_REQUIRED:
    "未找到 Claude Code 可执行文件。请将其安装到 THOTH 工具文件夹中。",
  CLAUDE_CODE_NOT_EXECUTABLE: "无法运行 Claude Code 可执行文件。请重新安装。",
  CLAUDE_CODE_CAPABILITY_UNSUPPORTED:
    "已安装的 Claude Code 版本过旧。请在 THOTH 工具文件夹中安装受支持的版本。",
  CLAUDE_CODE_LOGIN_START_FAILED: "Claude Code 无法开始登录。请稍后重试。",
  CLAUDE_CODE_LOGIN_REQUIRED: "请在设置的模型连接中登录 Claude，然后重试。",
  CLAUDE_CODE_LOGIN_FAILED: "Claude 登录没有完成。只有在你选择时才会开始新的登录。",
  CLAUDE_CODE_LOGIN_NOT_CONFIRMED: "Claude Code 已退出，但无法确认登录状态。请重试。",
  LOGIN_REQUIRED: "需要登录。",
  LOGIN_PENDING: "正在完成登录。",
  XAI_LOGIN_REQUIRED: "需要登录 xAI。",
  MODEL_CREDENTIAL_UNAVAILABLE: "没有登记 API 密钥。",
  MODEL_CREDENTIAL_STORE_UNREADABLE:
    "无法读取密钥文件，因此没有保存新密钥。请从备份恢复工作区 model-registry 文件夹中的 secrets.json 和 index.json，或将它们移到别处，然后重新登记。",
  MODEL_CREDENTIAL_STORE_UNWRITABLE:
    "无法保存密钥文件。之前登记的密钥保持不变。请检查磁盘空间和工作区文件夹的写入权限。",
  NODE_NPM_UNAVAILABLE:
    "自动安装需要 Node.js (npm)。请安装 Node.js LTS 后重试，或自己运行下面的命令。",
  CODEX_TOOL_INSTALL_FAILED: "Codex 工具安装失败。请检查互联网连接后重试。",
  CLAUDE_CODE_TOOL_INSTALL_FAILED: "Claude Code 安装失败。请检查互联网连接后重试。",
  CODEX_TOOL_INSTALL_TIMEOUT: "安装未能在五分钟内完成。请稍后重试。",
  CLAUDE_CODE_TOOL_INSTALL_TIMEOUT: "安装未能在五分钟内完成。请稍后重试。",
  MODEL_TOOL_INSTALL_TIMEOUT: "安装未能及时完成。请稍后检查其状态。",
  MODEL_TOOL_INSTALL_IN_PROGRESS: "此工具正在安装中。请等待其完成。",
  MODEL_TOOL_PREFIX_UNAVAILABLE: "找不到 THOTH 工具文件夹 (LOCALAPPDATA)。请自己运行下面的命令。",
  CATALOG_UNAVAILABLE: "登录成功，但模型列表暂时还不可用。",
  MODEL_CATALOG_REFRESH_TIMEOUT: "模型列表检查未能及时完成。请稍后重试。",
};

const traditional: Record<keyof typeof korean, string> = {
  CODEX_STANDALONE_NOT_INSTALLED: "這臺電腦上沒有 ChatGPT 登入所需的 Codex 連線工具。",
  CODEX_STANDALONE_PIN_UNAVAILABLE: "未找到所需版本（0.157.1）的 Codex 連線工具。",
  CODEX_PACKAGE_INTEGRITY_UNAVAILABLE: "無法驗證 Codex 的安裝資訊。請重新安裝該工具。",
  CODEX_PACKAGE_INTEGRITY_MISMATCH: "Codex 工具檔案與官方發行版不一致。請重新安裝該工具。",
  CODEX_PACKAGE_ROOT_ABSOLUTE_REQUIRED: "Codex 工具的位置設定無效。",
  UPDATE_REVIEW_REQUIRED: "Codex 工具的位置或版本已變化。請重新安裝該工具。",
  CODEX_PINNED_PLATFORM_UNSUPPORTED: "此作業系統暫不支援 ChatGPT 登入。請使用 API 金鑰連線。",
  CLAUDE_CLIENT_REGISTRATION_REQUIRED:
    "這臺電腦上尚未啟用 Claude 訂閱登入。目前可以使用 API 金鑰連線。",
  CLAUDE_CODE_NOT_INSTALLED: "這臺電腦上沒有 Claude 登入所需的 Claude Code。",
  CLAUDE_CODE_NATIVE_BINARY_REQUIRED:
    "未找到 Claude Code 可執行檔案。請將其安裝到 THOTH 工具資料夾中。",
  CLAUDE_CODE_NOT_EXECUTABLE: "無法執行 Claude Code 可執行檔案。請重新安裝。",
  CLAUDE_CODE_CAPABILITY_UNSUPPORTED:
    "已安裝的 Claude Code 版本過舊。請在 THOTH 工具資料夾中安裝受支援的版本。",
  CLAUDE_CODE_LOGIN_START_FAILED: "Claude Code 無法開始登入。請稍後重試。",
  CLAUDE_CODE_LOGIN_REQUIRED: "請在設定的模型連線中登入 Claude，然後重試。",
  CLAUDE_CODE_LOGIN_FAILED: "Claude 登入沒有完成。只有在你選擇時才會開始新的登入。",
  CLAUDE_CODE_LOGIN_NOT_CONFIRMED: "Claude Code 已退出，但無法確認登入狀態。請重試。",
  LOGIN_REQUIRED: "需要登入。",
  LOGIN_PENDING: "正在完成登入。",
  XAI_LOGIN_REQUIRED: "需要登入 xAI。",
  MODEL_CREDENTIAL_UNAVAILABLE: "沒有登記 API 金鑰。",
  MODEL_CREDENTIAL_STORE_UNREADABLE:
    "無法讀取金鑰檔案，因此沒有儲存新金鑰。請從備份還原工作區 model-registry 資料夾中的 secrets.json 和 index.json，或將它們移到別處，然後重新登記。",
  MODEL_CREDENTIAL_STORE_UNWRITABLE:
    "無法儲存金鑰檔案。之前登記的金鑰保持不變。請檢查磁碟空間和工作區資料夾的寫入權限。",
  NODE_NPM_UNAVAILABLE:
    "自動安裝需要 Node.js (npm)。請安裝 Node.js LTS 後重試，或自己執行下面的命令。",
  CODEX_TOOL_INSTALL_FAILED: "Codex 工具安裝失敗。請檢查網際網路連線後重試。",
  CLAUDE_CODE_TOOL_INSTALL_FAILED: "Claude Code 安裝失敗。請檢查網際網路連線後重試。",
  CODEX_TOOL_INSTALL_TIMEOUT: "安裝未能在五分鐘內完成。請稍後重試。",
  CLAUDE_CODE_TOOL_INSTALL_TIMEOUT: "安裝未能在五分鐘內完成。請稍後重試。",
  MODEL_TOOL_INSTALL_TIMEOUT: "安裝未能及時完成。請稍後檢查其狀態。",
  MODEL_TOOL_INSTALL_IN_PROGRESS: "此工具正在安裝中。請等待其完成。",
  MODEL_TOOL_PREFIX_UNAVAILABLE: "找不到 THOTH 工具資料夾 (LOCALAPPDATA)。請自己執行下面的命令。",
  CATALOG_UNAVAILABLE: "登入成功，但模型列表暫時還不可用。",
  MODEL_CATALOG_REFRESH_TIMEOUT: "模型列表檢查未能及時完成。請稍後重試。",
};
const japanese: Record<keyof typeof korean, string> = {
  CODEX_STANDALONE_NOT_INSTALLED:
    "ChatGPT のログインに必要な Codex 接続ツールが、このコンピューターにありません。",
  CODEX_STANDALONE_PIN_UNAVAILABLE:
    "必要なバージョン（0.157.1）の Codex 接続ツールが見つかりませんでした。",
  CODEX_PACKAGE_INTEGRITY_UNAVAILABLE:
    "Codex のインストール情報を確認できませんでした。ツールをインストールし直してください。",
  CODEX_PACKAGE_INTEGRITY_MISMATCH:
    "Codex ツールのファイルが公式の配布物と異なります。ツールをインストールし直してください。",
  CODEX_PACKAGE_ROOT_ABSOLUTE_REQUIRED: "Codex ツールの場所の設定が正しくありません。",
  UPDATE_REVIEW_REQUIRED:
    "Codex ツールの場所またはバージョンが変わりました。ツールをインストールし直してください。",
  CODEX_PINNED_PLATFORM_UNSUPPORTED:
    "このオペレーティングシステムでは、ChatGPT のログインにまだ対応していません。API キーで接続してください。",
  CLAUDE_CLIENT_REGISTRATION_REQUIRED:
    "Claude のサブスクリプションによるログインは、このコンピューターではまだ有効になっていません。現在は API キーで接続できます。",
  CLAUDE_CODE_NOT_INSTALLED:
    "Claude のログインに必要な Claude Code が、このコンピューターにありません。",
  CLAUDE_CODE_NATIVE_BINARY_REQUIRED:
    "Claude Code の実行ファイルが見つかりませんでした。THOTH のツールフォルダにインストールしてください。",
  CLAUDE_CODE_NOT_EXECUTABLE:
    "Claude Code の実行ファイルを実行できませんでした。インストールし直してください。",
  CLAUDE_CODE_CAPABILITY_UNSUPPORTED:
    "インストール済みの Claude Code のバージョンが古すぎます。THOTH のツールフォルダに、対応するバージョンをインストールしてください。",
  CLAUDE_CODE_LOGIN_START_FAILED:
    "Claude Code がログインを開始できませんでした。しばらくしてからもう一度お試しください。",
  CLAUDE_CODE_LOGIN_REQUIRED:
    "設定のモデル接続から Claude にサインインしてから、もう一度お試しください。",
  CLAUDE_CODE_LOGIN_FAILED:
    "Claude のログインが完了しませんでした。新しいログインは、選んだときにだけ始まります。",
  CLAUDE_CODE_LOGIN_NOT_CONFIRMED:
    "Claude Code は終了しましたが、ログインを確認できませんでした。もう一度お試しください。",
  LOGIN_REQUIRED: "ログインが必要です。",
  LOGIN_PENDING: "ログインを完了しています。",
  XAI_LOGIN_REQUIRED: "xAI のログインが必要です。",
  MODEL_CREDENTIAL_UNAVAILABLE: "API キーが登録されていません。",
  MODEL_CREDENTIAL_STORE_UNREADABLE:
    "キーのファイルを読み込めなかったため、新しいキーは保存していません。ワークスペースの model-registry フォルダにある secrets.json と index.json をバックアップから戻すか別の場所へ移してから、もう一度登録してください。",
  MODEL_CREDENTIAL_STORE_UNWRITABLE:
    "キーのファイルを保存できませんでした。これまでに登録したキーはそのままです。ディスクの空き容量とワークスペースのフォルダの書き込み権限を確認してください。",
  NODE_NPM_UNAVAILABLE:
    "自動インストールには Node.js (npm) が必要です。Node.js LTS をインストールしてからもう一度お試しいただくか、下のコマンドをご自身で実行してください。",
  CODEX_TOOL_INSTALL_FAILED:
    "Codex ツールのインストールに失敗しました。インターネット接続を確認して、もう一度お試しください。",
  CLAUDE_CODE_TOOL_INSTALL_FAILED:
    "Claude Code のインストールに失敗しました。インターネット接続を確認して、もう一度お試しください。",
  CODEX_TOOL_INSTALL_TIMEOUT:
    "インストールが5分以内に終わりませんでした。しばらくしてからもう一度お試しください。",
  CLAUDE_CODE_TOOL_INSTALL_TIMEOUT:
    "インストールが5分以内に終わりませんでした。しばらくしてからもう一度お試しください。",
  MODEL_TOOL_INSTALL_TIMEOUT:
    "インストールが時間内に終わりませんでした。しばらくしてから状態を確認してください。",
  MODEL_TOOL_INSTALL_IN_PROGRESS:
    "このツールはすでにインストール中です。終わるまでお待ちください。",
  MODEL_TOOL_PREFIX_UNAVAILABLE:
    "THOTH のツールフォルダ（LOCALAPPDATA）の場所を特定できませんでした。下のコマンドをご自身で実行してください。",
  CATALOG_UNAVAILABLE: "ログインには成功しましたが、モデル一覧はまだ利用できません。",
  MODEL_CATALOG_REFRESH_TIMEOUT:
    "モデル一覧の確認が時間内に終わりませんでした。しばらくしてからもう一度お試しください。",
};

export function connectionReason(code: string): string | undefined {
  const values: Readonly<Record<string, string>> = pickLocale({
    ko: korean,
    en: english,
    "zh-Hans": chinese,
    "zh-Hant": traditional,
    ja: japanese,
  });
  return values[code];
}
