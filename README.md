# 📱 语音识别API接口文档

使用命令 `docker-compose up --build` 进行docker部署

## 📋 目录
1. [概述](#概述)
2. [实时语音识别WebSocket接口](#实时语音识别websocket接口)
3. [文件上传识别WebSocket接口](#文件上传识别websocket接口)
4. [HTTP API接口](#http-api接口)
5. [技术规范](#技术规范)
6. [错误处理](#错误处理)

## 🎯 概述

语音识别服务API，提供实时语音识别和文件识别功能。

### API类型
- 🎤 **实时语音识别**：WebSocket流式API，支持实时对话
- 📁 **文件上传识别**：WebSocket/HTTP API，支持音频文件识别
- 🤖 **智能对话**：集成LLM，提供上下文对话能力
- 📊 **系统管理**：配置管理、状态查询等API

### 服务端点
- **通信端口**: HTTP/WS：`8000`  HTTPS/WSS：`32796`
- **WebSocket端点**：
  - `/ws/stream` - 实时语音识别
  - `/ws/upload` - 文件流式识别
- **HTTP API端点**：
  - `/api/recognize` - 文件识别
  - `/api/config` - 配置管理
  - `/api/pool/stats` - 连接池统计
  - `/api/cleanup` - 用户清理
- **额外信息**:
  - Web客户端：`http://your-server.com:port`
  - 后台管理：`http://your-server.com:port/admin`
  - 默认管理员账户：`admin` `admin`

---

## 📡 实时语音识别WebSocket接口

### 连接端点
```
wss://your-server.com:port/ws/stream
```

### 客户端发送的消息类型

#### 1. 音频数据传输
```json
// 方式1: JSON格式 (推荐)
{
    "type": "audio_data",              // 消息类型，固定值
    "data": "base64_encoded_audio_data" // Base64编码的音频数据
}

// 方式2: 直接发送二进制数据
// 直接发送PCM音频字节流（16bit, 16kHz, 单声道）
```

#### 2. 重置对话历史
```json
{
    "type": "reset_conversation"       // 消息类型，固定值
}
```

#### 3. 测试LLM连接
```json
{
    "type": "test_llm"                 // 消息类型，固定值
}
```

### 服务器发送的消息类型

#### 1. 连接状态消息

##### 用户连接成功
```json
{
    "type": "user_connected",          // 消息类型
    "user_id": "unique_user_id",       // 分配的用户ID
    "active_users": 5                  // 当前在线用户数
}
```

##### ASR连接成功（连接池模式）
```json
{
    "type": "asr_connected",           // 消息类型
    "message": "ASR服务器连接成功（连接池模式）", // 状态描述
    "connection_mode": "pool",         // 连接模式: "pool" | "independent"
    "pool_stats": {                    // 连接池统计信息
        "total_connections": 10,        // 总连接数
        "active_connections": 3,        // 活跃连接数 
        "idle_connections": 7,          // 空闲连接数
        "active_users": 5,              // 活跃用户数
        "max_connections": 10,          // 最大连接数
        "min_connections": 2            // 最小连接数
    }
}
```

##### ASR连接成功（独立连接模式）
```json
{
    "type": "asr_connected",           // 消息类型
    "message": "ASR服务器连接成功（独立连接模式）", // 状态描述
    "connection_mode": "independent",  // 连接模式
    "config": {                        // FunASR配置信息
        "mode": "2pass",               // 识别模式
        "chunk_size": [5, 10, 5],      // 块大小配置
        "audio_fs": 16000,             // 音频采样率
        "wav_format": "pcm"            // 音频格式
    }
}
```

#### 2. 语音识别消息

##### 部分识别结果
```json
{
    "type": "recognition_partial",     // 消息类型
    "text": "你好"                     // 部分识别的文本
}
```

##### 最终识别结果  
```json
{
    "type": "recognition_final",       // 消息类型
    "text": "你好世界！"               // 最终识别的文本
}
```

#### 3. AI对话消息

##### AI开始回答
```json
{
    "type": "ai_start",                // 消息类型
    "user_text": "你好世界！",         // 用户输入的文本
    "message": "AI正在思考..."         // 状态消息
}
```

##### AI回答片段
```json
{
    "type": "ai_chunk",                // 消息类型
    "content": "你好！"                // AI回答的内容片段
}
```

##### AI回答完成
```json
{
    "type": "ai_complete",             // 消息类型
    "full_response": "你好！很高兴与您对话。" // AI的完整回答
}
```

#### 4. 系统状态消息

##### 对话重置确认
```json
{
    "type": "conversation_reset",      // 消息类型
    "message": "对话历史已重置"        // 确认消息
}
```

##### LLM测试结果
```json
{
    "type": "llm_test_result",         // 消息类型
    "result": {                        // 测试结果
        "success": true,               // 是否成功
        "message": "连接正常"          // 结果描述
    }
}
```

#### 5. 错误消息

##### ASR连接失败
```json
{
    "type": "asr_connection_failed",   // 消息类型
    "message": "无法连接到ASR服务器，请检查服务状态", // 错误描述
    "error": "Connection timeout"      // 具体错误信息
}
```

##### ASR重连失败
```json
{
    "type": "asr_reconnect_failed",    // 消息类型
    "message": "ASR服务重连失败",      // 错误描述
    "error": "Max retries exceeded"    // 具体错误信息
}
```

##### AI服务错误
```json
{
    "type": "ai_error",                // 消息类型
    "error": "AI服务暂时不可用"        // 错误描述
}
```

---

## 📁 文件上传识别WebSocket接口

### 连接端点
```
wss://your-server.com:port/ws/upload
```

### 客户端发送的消息类型

#### 1. Base64音频上传
```json
{
    "type": "upload_audio",            // 消息类型，固定值
    "audio_data": "base64_encoded_audio", // Base64编码的音频数据
    "filename": "recording.wav"        // 文件名 (可选)
}
```

#### 2. 二进制音频上传
```
// 直接发送音频文件的二进制数据
WebSocket.send(audioFileArrayBuffer)
```

### 服务器发送的消息类型

#### 1. 文件处理流程消息

##### 文件接收确认
```json
{
    "type": "file_received",           // 消息类型
    "size": 1024000,                   // 文件大小
    "message": "开始处理音频文件..."   // 处理状态
}
```

##### 音频处理状态
```json
{
    "type": "processing",              // 消息类型
    "message": "音频信息: wav 格式，大小: 1024000 字节" // 处理状态描述
}

{
    "type": "processing",              // 消息类型
    "message": "音频处理完成，开始语音识别...", // 处理状态描述
    "processed_size": 512000,          // 处理后大小
    "sample_rate": 16000               // 采样率
}
```

##### 识别开始通知
```json
{
    "type": "recognition_start",       // 消息类型
    "message": "连接到FunASR服务，开始识别..." // 状态描述
}
```

#### 2. 流式识别结果消息

##### 实时识别结果
```json
{
    "type": "recognition_partial",     // 消息类型
    "text": "你好",                    // 识别文本
    "mode": "2pass-online"             // 识别模式
}
```

##### 识别片段结果
```json
{
    "type": "recognition_segment",     // 消息类型
    "text": "你好世界",                // 片段文本
    "accumulated": "你好世界",         // 累积文本
    "mode": "2pass-offline"            // 识别模式
}
```

#### 3. 进度和完成消息

##### 上传进度更新
```json
{
    "type": "upload_progress",         // 消息类型
    "progress": 85.5,                  // 进度百分比
    "current": 85,                     // 当前块数
    "total": 100                       // 总块数
}

// 或者简单的消息格式
{
    "type": "upload_progress",         // 消息类型
    "message": "开始处理音频文件...",   // 进度描述
    "filename": "recording.wav"        // 文件名 (可选)
}
```

##### 上传完成
```json
{
    "type": "upload_complete",         // 消息类型
    "message": "音频发送完成，等待最终识别结果..." // 完成状态
}
```

#### 4. LLM处理消息

##### LLM处理开始
```json
{
    "type": "llm_start",               // 消息类型
    "message": "开始AI回复生成..."     // 状态描述
}
```

##### LLM回答片段
```json
{
    "type": "llm_chunk",               // 消息类型
    "chunk": "你好！"                  // LLM回答片段
}
```

##### LLM处理完成
```json
{
    "type": "llm_complete",            // 消息类型
    "recognized_text": "你好世界",     // 识别的文本
    "llm_response": "你好！很高兴与您对话。" // LLM完整回答
}
```

#### 5. 错误消息

##### 上传错误
```json
{
    "type": "upload_error",            // 消息类型
    "error": "缺少音频数据"            // 错误描述
}
```

##### LLM错误
```json
{
    "type": "llm_error",               // 消息类型
    "error": "AI服务暂时不可用"        // 错误描述
}
```

##### 通用错误
```json
{
    "type": "error",                   // 消息类型
    "message": "处理失败: 音频格式不支持" // 错误描述
}
```

---

## 🌐 HTTP API接口

### 1. 音频识别API

#### 接口地址
```
POST /api/recognize/
```

#### 请求格式
```http
Content-Type: multipart/form-data

audio: [音频文件]
```

#### 响应格式
```json
{
    "success": true,                   // 请求是否成功
    "text": "识别出的文字内容",        // 语音识别结果
    "llm_response": "AI生成的回答",    // LLM生成的回答
    "debug_info": {                    // 调试信息
        "original_size": 1024000,      // 原始文件大小
        "processed_size": 512000,      // 处理后大小
        "sample_rate": 16000,          // 采样率
        "filename": "audio.wav",       // 原始文件名
        "audio_info": {                // 音频详细信息
            "format": "wav",           // 音频格式
            "channels": 1,             // 声道数
            "duration": 5.2            // 时长秒数
        }
    }
}
```

#### 错误响应
```json
{
    "success": false,                  // 操作失败
    "error": "未提供音频文件"          // 错误描述
}
```

### 2. 配置获取API

#### 接口地址
```
GET /api/config/
```

#### 响应格式
```json
{
    "max_conversation_history": 5      // 最大对话历史数量
}
```

#### 错误响应
```json
{
    "success": false,                  // 操作失败
    "error": "获取配置失败: [具体错误]" // 错误描述
}
```

### 3. 用户清理API

#### 接口地址
```
POST /api/cleanup/
```

#### 请求格式
```json
{
    "inactive_hours": 24               // 清理多少小时前的非活跃会话 (Integer, 可选)
}
```

#### 响应格式
```json
{
    "success": true,                   // 操作是否成功
    "message": "成功清理 5 个非活跃用户会话", // 结果描述
    "cleaned_count": 5,                // 清理的会话数
    "remaining_users": 10              // 剩余用户数
}
```

#### 错误响应
```json
{
    "success": false,                  // 操作失败
    "error": "无效的JSON数据"          // 错误描述
}
```

### 4. 连接池状态API

#### 接口地址
```
GET /api/pool/stats/
```

#### 响应格式
```json
{
    "success": true,                   // 操作是否成功
    "stats": {                         // 连接池统计
        "total_connections": 10,       // 总连接数
        "active_connections": 3,       // 活跃连接数
        "idle_connections": 7,         // 空闲连接数
        "active_users": 5,             // 活跃用户数
        "max_connections": 10,         // 最大连接数
        "min_connections": 2           // 最小连接数
    },
    "message": "连接池状态获取成功"     // 状态描述
}
```

#### 错误响应
```json
{
    "success": false,                  // 操作失败
    "error": "获取连接池状态失败: [具体错误]" // 错误描述
}
```

---

## 🔧 技术规范

### 音频参数要求
- **采样率**: 16kHz (推荐标准)
- **声道数**: 1 (单声道)
- **位深度**: 16位
- **格式**: PCM或支持的压缩格式 (WAV, MP3, M4A, WebM, OGG)

### 数据传输规范
- **WebSocket数据块大小**: 建议4KB
- **发送频率**: 建议100ms间隔
- **编码格式**: Base64 (JSON模式) 或 二进制流

### 连接管理要求
- **自动重连**: 建议最多重试3次
- **连接超时**: 建议5秒
- **响应超时**: 建议10秒
- **心跳机制**: 建议30秒间隔

---

## ⚠️ 错误处理

### 实时语音识别错误类型

| 错误类型 | 触发场景 | 解决方案 |
|---------|----------|----------|
| `asr_connection_failed` | 初始连接FunASR服务器时失败 | 检查网络连接，重试连接 |
| `asr_reconnect_failed` | ASR连接断开后重连尝试失败 | 手动重新连接或刷新页面 |
| `ai_error` | LLM调用失败或异常 | 稍后重试，或联系技术支持 |

### 文件上传识别错误类型

| 错误类型 | 触发场景 | 解决方案 |
|---------|----------|----------|
| `upload_error` | Base64音频上传时缺少audio_data字段<br/>音频文件处理失败<br/>识别结果为空 | 检查音频文件格式，确保上传完整的音频数据 |
| `llm_error` | 文件上传识别完成后，LLM处理失败 | 排查后端服务是否正常 |
| `error` | 流式识别过程中的各种异常 | 根据具体错误信息进行相应处理 |

### HTTP API错误状态码

- **400**: 请求参数错误（如未提供音频文件、无效JSON数据）
- **500**: 服务器内部错误（处理异常、服务不可用等）

