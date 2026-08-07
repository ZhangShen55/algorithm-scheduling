// app.js

// 页面加载完成后立即执行
document.addEventListener('DOMContentLoaded', () => {
    // 1. 加载已存在的人物列表
    loadPersons();

    // 2. 绑定“添加人物”表单的提交事件
    const addPersonForm = document.getElementById('add-person-form');
    addPersonForm.addEventListener('submit', handleAddPerson);

    // 3. 绑定"在线识别"表单的提交事件
    const recognitionForm = document.getElementById('recognition-form');
    if (recognitionForm) {
        recognitionForm.addEventListener('submit', handleRecognizeFace);
        console.log('[初始化] 单帧识别表单事件已绑定');
    } else {
        console.error('[初始化] 未找到 recognition-form 元素');
    }

    // 4. 为识别图片输入框添加预览功能
    const recognitionImageInput = document.getElementById('recognition-image');
    recognitionImageInput.addEventListener('change', (event) => {
        const preview = document.getElementById('image-preview');
        const file = event.target.files[0];
        if (file) {
            preview.src = URL.createObjectURL(file);
            preview.style.display = 'block';
        }
    });

    // 5. 绑定"多帧识别"表单的提交事件
    const batchRecognitionForm = document.getElementById('batch-recognition-form');
    if (batchRecognitionForm) {
        batchRecognitionForm.addEventListener('submit', handleBatchRecognizeFace);
        console.log('[初始化] 多帧识别表单事件已绑定');
    } else {
        console.error('[初始化] 未找到 batch-recognition-form 元素');
    }

    // 6. 为多帧识别图片输入框添加文件数量显示
    const batchRecognitionImagesInput = document.getElementById('batch-recognition-images');
    if (batchRecognitionImagesInput) {
        batchRecognitionImagesInput.addEventListener('change', (event) => {
            const files = event.target.files;
            const container = document.getElementById('batch-preview-container');
            const countSpan = document.getElementById('batch-image-count');
            if (files.length > 0) {
                countSpan.textContent = files.length;
                container.style.display = 'block';
            } else {
                container.style.display = 'none';
            }
        });
        console.log('[初始化] 多帧图片选择监听已绑定');
    } else {
        console.error('[初始化] 未找到 batch-recognition-images 元素');
    }

    // 7. 以下是批量入库
    const batchBtn = document.getElementById('btn-batch-upload');
    if (batchBtn) batchBtn.addEventListener('click', handleBatchUpload);
    // 绑定查询表单
    const searchForm = document.getElementById('search-person-form');
    searchForm.addEventListener('submit', handleSearchPerson);

    // 绑定删除按钮点击事件
    const deleteBtn = document.getElementById('confirm-delete');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', handleDeletePerson);  // 绑定删除按钮事件
    }
    // 以上是添加

});

// 记录待删除的人物 ID
let personToDelete = null;

// 显示删除确认模态框
function showDeleteModal(personId, personName) {
    personToDelete = personId;  // 保存待删除人物的 ID
    const modal = new bootstrap.Modal(document.getElementById('delete-person-modal'));  // 初始化模态框
    document.getElementById('deletePersonModalLabel').textContent = `确认删除 ${personName}`;
    modal.show();  // 显示模态框
}

// 绑定删除按钮点击事件
async function handleDeletePerson() {
    if (!personToDelete) {
        console.error("No person selected to delete.");
        return;  // 如果没有设置待删除的 ID，则返回
    }
    console.log(`Attempting to delete person with ID: ${personToDelete}`);
    try {
        const response = await fetch('/persons/by_id', {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ id: personToDelete })
        });
        const result = await response.json();

        if (!response.ok) {
            alert(result.detail || '删除失败');
            return;
        }

        alert(`成功删除人物: ${result.message}`);
        // 关闭模态框
        const modal = bootstrap.Modal.getInstance(document.getElementById('delete-person-modal'));
        if (modal) modal.hide();
        // 重新加载人物列表
        loadPersons();
    } catch (error) {
        console.error('Error deleting person:', error);
        alert('删除失败: ' + error.message);
    } finally {
        personToDelete = null; // 清空删除人物的 ID
    }
}


// 点击关闭或取消按钮时清理 personToDelete
document.querySelectorAll('[data-bs-dismiss="modal"]').forEach(button => {
    button.addEventListener('click', () => {
        personToDelete = null; // 清空待删除的人物 ID
    });
});

// 简单的工具：更新进度条与统计
function updateBatchUI(done, totalImages, success, fail, skip, totalFiles, statusText = '') {
    const stats = document.getElementById('batch-stats');
    stats.style.display = 'block';
    document.getElementById('batch-total').textContent = totalFiles;
    document.getElementById('batch-images').textContent = totalImages;
    document.getElementById('batch-success').textContent = success;
    document.getElementById('batch-fail').textContent = fail;
    document.getElementById('batch-skip').textContent = skip;

    // 更新状态文本
    const statusEl = document.getElementById('batch-current-status');
    if (statusText) {
        statusEl.textContent = statusText;
    }

    const pct = totalImages ? Math.round((done / totalImages) * 100) : 0;
    const bar = document.getElementById('batch-progressbar');
    bar.style.width = pct + '%';
    bar.textContent = pct + '%';
}



// 显示提示信息的辅助函数
function showAlert(message, type = 'danger', containerId) {
    const container = document.getElementById(containerId);
    const wrapper = document.createElement('div');
    wrapper.innerHTML = [
        `<div class="alert alert-${type} alert-dismissible" role="alert">`,
        `   <div>${message}</div>`,
        '   <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>',
        '</div>'
    ].join('');
    container.append(wrapper);
}


// 函数：加载人物列表
async function loadPersons() {
    try {
        const response = await fetch('/persons');
        if (!response.ok) throw new Error('获取人物列表失败');
        
        const persons = await response.json();
        const personListDiv = document.getElementById('person-list');
        personListDiv.innerHTML = ''; // 清空现有列表

        persons.forEach(person => {
            const col = document.createElement('div');
            col.className = 'col';
            col.innerHTML = `
                <div class="card h-100">
                    <img src="${person.photo_path}" class="card-img-top" alt="${person.name}">
                    <div class="card-body">
                        <h6 class="card-title text-center">${person.name}</h6>
                    </div>
                </div>
            `;
            personListDiv.appendChild(col);
        });
    } catch (error) {
        console.error('Error loading persons:', error);
        showAlert(error.message, 'danger', 'alert-container-add');
    }
}

// 函数：处理添加人物的表单提交
async function handleAddPerson(event) {
    event.preventDefault(); // 阻止表单默认的刷新页面行为

    const form = event.target;
    const name = document.getElementById('person-name').value;
    const number = document.getElementById('person-number').value;
    const photoInput = document.getElementById('person-photo');
    const photoFile = photoInput.files[0];

    if (!photoFile) {
        showAlert('请选择照片文件', 'danger', 'alert-container-add');
        return;
    }

    try {
        // 将图片转换为 Base64
        const photoBase64 = await fileToBase64(photoFile);

        const requestBody = {
            name: name,
            number: number,
            photo: photoBase64
        };

        const response = await fetch('/persons', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestBody)
        });

        const result = await response.json();

        if (!response.ok) {
            // 如果后端返回错误信息（如：未检测到人脸）
            throw new Error(result.detail || '添加失败，请检查输入。');
        }

        showAlert(`人物 "${result.name}" 添加成功! ${result.tip ? '提示: ' + result.tip : ''}`, 'success', 'alert-container-add');
        form.reset(); // 清空表单
        loadPersons(); // 重新加载人物列表
    } catch (error) {
        console.error('Error adding person:', error);
        showAlert(error.message, 'danger', 'alert-container-add');
    }
}

// 工具函数：将文件转换为 Base64
function fileToBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = error => reject(error);
        reader.readAsDataURL(file);
    });
}

async function handleRecognizeFace(event) {
    console.log('[单帧识别] 函数被调用');
    event.preventDefault();

    const photoInput = document.getElementById('recognition-image');
    const targetsInput = document.getElementById('recognition-targets');
    const photoFile = photoInput.files[0];
    const resultCard = document.getElementById('recognition-result-card');
    const detectedCard = document.getElementById('detected-face-card');

    console.log('[单帧识别] photoInput元素:', photoInput);
    console.log('[单帧识别] targetsInput元素:', targetsInput);
    console.log('[单帧识别] photoFile:', photoFile);

    if (!photoFile) {
        showAlert('请选择图片文件', 'danger', 'alert-container-rec');
        return;
    }

    try {
        // 将图片转换为 Base64
        const photoBase64 = await fileToBase64(photoFile);

        // 解析 targets 输入
        const targetsText = targetsInput.value.trim();
        const targets = targetsText ? targetsText.split(',').map(t => t.trim()).filter(t => t) : [];

        console.log('[单帧识别] targets输入:', targetsText);
        console.log('[单帧识别] 解析后的targets:', targets);

        const requestBody = {
            photo: photoBase64,
            targets: targets,  // 候选人列表
            threshold: null  // 使用默认阈值
        };

        console.log('[单帧识别] 发送请求到 /recognize');

        const response = await fetch('/recognize', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestBody)
        });

        const result = await response.json();

        // HTTP 500/400 错误处理
        if (!response.ok) {
            detectedCard.style.display = 'none';
            resultCard.style.display = 'none';
            throw new Error(result.detail || '识别失败');
        }

        // HTTP 200 业务响应处理
        const statusEl = document.getElementById('result-status');
        const nameEl = document.getElementById('result-name');
        const simEl = document.getElementById('result-similarity');

        // 1. 检查是否检测到人脸
        if (!result.has_face) {
            detectedCard.style.display = 'none';
            resultCard.style.display = 'block';
            nameEl.textContent = '未检测到人脸';
            simEl.textContent = '';
            statusEl.textContent = result.message || '图像中未检测到人脸，请重新捕捉';
            statusEl.className = 'card-text text-warning';
            return;
        }

        // 2. 显示检测到的人脸框（如果有）
        if (result.bbox) {
            const bboxText = `人脸位置: x=${result.bbox.x}, y=${result.bbox.y}, w=${result.bbox.w}, h=${result.bbox.h}`;
            document.getElementById('detected-bbox').textContent = bboxText;
            detectedCard.style.display = 'block';
        }

        // 3. 显示识别结果
        resultCard.style.display = 'block';

        if (result.match && result.match.length > 0) {
            // 匹配成功 - 显示所有匹配结果
            const topMatch = result.match[0]; // 最相似的人物
            nameEl.textContent = `${topMatch.name || '未知'} (${topMatch.number || ''})`;
            simEl.textContent = `相似度: ${topMatch.similarity}`;
            statusEl.textContent = result.message || '识别成功';
            statusEl.className = 'card-text text-success';

            // 如果有多个匹配结果，显示其他候选
            if (result.match.length > 1) {
                let otherMatches = '<div class="mt-2"><small class="text-muted">其他候选:</small><ul class="list-unstyled small">';
                for (let i = 1; i < result.match.length; i++) {
                    const m = result.match[i];
                    const targetBadge = m.is_target ? '<span class="badge bg-info">目标</span> ' : '';
                    otherMatches += `<li>${i}. ${targetBadge}${m.name || '未知'} (${m.number || ''}) - ${m.similarity}</li>`;
                }
                otherMatches += '</ul></div>';

                // 在相似度下方插入其他候选
                if (!document.getElementById('other-matches-container')) {
                    const container = document.createElement('div');
                    container.id = 'other-matches-container';
                    simEl.parentElement.appendChild(container);
                }
                document.getElementById('other-matches-container').innerHTML = otherMatches;
            } else {
                // 清除之前的其他候选
                const container = document.getElementById('other-matches-container');
                if (container) container.innerHTML = '';
            }

            // 显示匹配人物的照片
            const resultPhoto = document.getElementById('result-photo');
            if (topMatch.id) {
                // 这里假设可以通过 ID 获取照片，实际需根据后端调整
                resultPhoto.style.display = 'block';
            }
        } else {
            // 未匹配
            nameEl.textContent = '未匹配到人物';
            simEl.textContent = `阈值: ${(result.threshold * 100).toFixed(2)}%`;
            statusEl.textContent = result.message || '未能匹配到已知人物';
            statusEl.className = 'card-text text-warning';
            document.getElementById('result-photo').style.display = 'none';

            // 清除之前的其他候选
            const container = document.getElementById('other-matches-container');
            if (container) container.innerHTML = '';
        }

    } catch (error) {
        console.error('Error recognizing face:', error);
        resultCard.style.display = 'none';
        detectedCard.style.display = 'none';
        showAlert(error.message, 'danger', 'alert-container-rec');
    }
}


// 多帧识别处理函数
async function handleBatchRecognizeFace(event) {
    console.log('[多帧识别] 函数被调用');
    event.preventDefault();
    event.stopPropagation();

    const photosInput = document.getElementById('batch-recognition-images');
    const targetsInput = document.getElementById('batch-recognition-targets');
    const photoFiles = Array.from(photosInput.files || []);
    const resultCard = document.getElementById('recognition-result-card');
    const detectedCard = document.getElementById('detected-face-card');

    console.log('[多帧识别] photosInput元素:', photosInput);
    console.log('[多帧识别] photoFiles:', photoFiles);

    if (photoFiles.length === 0) {
        showAlert('请选择至少一张图片文件', 'danger', 'alert-container-rec');
        return;
    }

    console.log('[多帧识别] 选择的图片数量:', photoFiles.length);

    try {
        // 将所有图片转换为 Base64
        console.log('[多帧识别] 开始转换图片为Base64...');
        const photosBase64 = await Promise.all(
            photoFiles.map(file => fileToBase64(file))
        );
        console.log('[多帧识别] Base64转换完成');

        // 解析 targets 输入
        const targetsText = targetsInput.value.trim();
        const targets = targetsText ? targetsText.split(',').map(t => t.trim()).filter(t => t) : [];

        console.log('[多帧识别] targets输入:', targetsText);
        console.log('[多帧识别] 解析后的targets:', targets);

        const requestBody = {
            photos: photosBase64,  // 多张图片的 Base64 数组
            targets: targets,
            threshold: null
        };

        console.log('[多帧识别] 发送请求到 /recognize/batch, 图片数:', photosBase64.length);

        const response = await fetch('/recognize/batch', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestBody)
        });

        const result = await response.json();
        console.log('[多帧识别] 响应状态:', response.status);
        console.log('[多帧识别] 响应结果:', result);

        // HTTP 500/400 错误处理
        if (!response.ok) {
            detectedCard.style.display = 'none';
            resultCard.style.display = 'none';
            throw new Error(result.detail || '多帧识别失败');
        }

        // HTTP 200 业务响应处理
        const statusEl = document.getElementById('result-status');
        const nameEl = document.getElementById('result-name');
        const simEl = document.getElementById('result-similarity');

        // 隐藏检测到的人脸框（多帧识别不显示单个bbox）
        detectedCard.style.display = 'none';

        // 显示识别结果
        resultCard.style.display = 'block';

        // 构建置信度和帧数信息
        const frameInfo = `有效帧数: ${result.valid_frames}/${result.total_frames} (置信度: ${(result.confidence * 100).toFixed(1)}%)`;

        if (result.match && result.match.length > 0) {
            // 匹配成功 - 显示所有匹配结果
            const topMatch = result.match[0];
            nameEl.textContent = `${topMatch.name || '未知'} (${topMatch.number || ''})`;
            simEl.textContent = `相似度: ${topMatch.similarity} | ${frameInfo}`;
            statusEl.textContent = result.message || '多帧识别成功';
            statusEl.className = 'card-text text-success';

            // 如果有多个匹配结果，显示其他候选
            if (result.match.length > 1) {
                let otherMatches = '<div class="mt-2"><small class="text-muted">其他候选:</small><ul class="list-unstyled small">';
                for (let i = 1; i < result.match.length; i++) {
                    const m = result.match[i];
                    const targetBadge = m.is_target ? '<span class="badge bg-info">目标</span> ' : '';
                    otherMatches += `<li>${i}. ${targetBadge}${m.name || '未知'} (${m.number || ''}) - ${m.similarity}</li>`;
                }
                otherMatches += '</ul></div>';

                // 在相似度下方插入其他候选
                if (!document.getElementById('other-matches-container')) {
                    const container = document.createElement('div');
                    container.id = 'other-matches-container';
                    simEl.parentElement.appendChild(container);
                }
                document.getElementById('other-matches-container').innerHTML = otherMatches;
            } else {
                const container = document.getElementById('other-matches-container');
                if (container) container.innerHTML = '';
            }

            // 显示帧处理详情（可选）
            if (result.frames && result.frames.length > 0) {
                let frameDetails = '<div class="mt-2"><small class="text-muted">帧处理详情:</small><ul class="list-unstyled small">';
                result.frames.forEach(frame => {
                    const status = frame.has_face ? '✓ 有效' : `✗ ${frame.error || '无人脸'}`;
                    frameDetails += `<li>帧${frame.index + 1}: ${status}</li>`;
                });
                frameDetails += '</ul></div>';

                if (!document.getElementById('frame-details-container')) {
                    const container = document.createElement('div');
                    container.id = 'frame-details-container';
                    statusEl.parentElement.appendChild(container);
                }
                document.getElementById('frame-details-container').innerHTML = frameDetails;
            }

        } else {
            // 未匹配
            nameEl.textContent = '未匹配到人物';
            simEl.textContent = `阈值: ${(result.threshold * 100).toFixed(2)}% | ${frameInfo}`;
            statusEl.textContent = result.message || '多帧识别未能匹配到已知人物';
            statusEl.className = 'card-text text-warning';

            // 清除之前的其他候选和帧详情
            const matchContainer = document.getElementById('other-matches-container');
            if (matchContainer) matchContainer.innerHTML = '';
            const frameContainer = document.getElementById('frame-details-container');
            if (frameContainer) frameContainer.innerHTML = '';
        }

    } catch (error) {
        console.error('Error batch recognizing face:', error);
        resultCard.style.display = 'none';
        detectedCard.style.display = 'none';
        showAlert(error.message, 'danger', 'alert-container-rec');
    }
}


// 批量上传核心逻辑：使用 /persons/batch 接口
// 文件名格式：{name}_{number}.jpg
// 示例：张三_t123.jpg → name: 张三, number: t123
// 分批处理：每批 50 张图片，避免单次请求过大导致超时
async function handleBatchUpload() {
    const input = document.getElementById('folder-input');
    const files = Array.from(input.files || []);
    const alertBoxId = 'alert-container-batch';
    const BATCH_SIZE = 50;  // 每批处理 50 张图片

    if (!files.length) {
        showAlert('请先选择一个包含图片的文件夹。', 'warning', alertBoxId);
        return;
    }

    // 过滤掉以点（.）开头的隐藏文件
    const imageFiles = files.filter(f => !f.name.startsWith('.'));

    // 分类：图片 vs 非图片
    const isImage = (f) => f.type.startsWith('image/') ||
        /\.(jpg|jpeg|png|bmp|webp)$/i.test(f.name);
    const validFiles = imageFiles.filter(isImage);
    let skipCount = imageFiles.length - validFiles.length;

    if (validFiles.length === 0) {
        showAlert('所选文件夹中没有有效的图片文件。', 'warning', alertBoxId);
        return;
    }

    // 初始化进度显示
    updateBatchUI(0, validFiles.length, 0, 0, skipCount, files.length, '正在准备图片...');

    try {
        // 第一步：解析所有文件名并转换为 Base64
        const personsData = [];
        const invalidFiles = [];
        let encodedCount = 0;

        updateBatchUI(0, validFiles.length, 0, 0, skipCount, files.length, '正在读取和编码图片文件...');

        for (const file of validFiles) {
            try {
                // 提取文件名（去掉扩展名）
                const nameWithoutExt = file.name.replace(/\.[^.]+$/, '');

                // 解析文件名：{name}_{number}
                const match = nameWithoutExt.match(/^(.+?)_(.+)$/);

                if (!match) {
                    invalidFiles.push(`${file.name} (格式不符合 {name}_{number})`);
                    skipCount++;
                    continue;
                }

                const name = match[1].trim();
                const number = match[2].trim();

                if (!number || !name) {
                    invalidFiles.push(`${file.name} (number 或 name 为空)`);
                    skipCount++;
                    continue;
                }

                const photoBase64 = await fileToBase64(file);

                personsData.push({
                    name: name,
                    number: number,
                    photo: photoBase64,
                    fileName: file.name  // 保存原始文件名用于错误提示
                });

                encodedCount++;
                // 更新编码进度
                if (encodedCount % 10 === 0 || encodedCount === validFiles.length - skipCount) {
                    updateBatchUI(
                        0,
                        validFiles.length,
                        0,
                        0,
                        skipCount,
                        files.length,
                        `正在编码图片：${encodedCount}/${validFiles.length - skipCount}`
                    );
                }
            } catch (e) {
                console.error('读取文件失败:', file.name, e);
                invalidFiles.push(`${file.name} (读取失败: ${e.message})`);
                skipCount++;
            }
        }

        if (personsData.length === 0) {
            showAlert('没有符合格式的图片文件。文件名格式应为：{name}_{number}.jpg', 'warning', alertBoxId);
            if (invalidFiles.length > 0) {
                showAlert(`跳过的文件：<br>${invalidFiles.join('<br>')}`, 'info', alertBoxId);
            }
            return;
        }

        // 第二步：分批上传
        const totalBatches = Math.ceil(personsData.length / BATCH_SIZE);
        let totalSuccess = 0;
        let totalFail = 0;
        const allFailedFiles = [];

        updateBatchUI(
            0,
            personsData.length,
            0,
            0,
            skipCount,
            files.length,
            `准备上传 ${personsData.length} 张图片，共分 ${totalBatches} 批...`
        );

        for (let batchIndex = 0; batchIndex < totalBatches; batchIndex++) {
            const start = batchIndex * BATCH_SIZE;
            const end = Math.min(start + BATCH_SIZE, personsData.length);
            const currentBatch = personsData.slice(start, end);

            // 更新状态：正在上传当前批次
            updateBatchUI(
                start,
                personsData.length,
                totalSuccess,
                totalFail,
                skipCount,
                files.length,
                `正在上传第 ${batchIndex + 1}/${totalBatches} 批（${start + 1}-${end}/${personsData.length}）...`
            );

            try {
                // 调用批量接口
                const response = await fetch('/persons/batch', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ persons: currentBatch })
                });

                const result = await response.json();

                if (!response.ok) {
                    throw new Error(result.detail || `第 ${batchIndex + 1} 批上传失败`);
                }

                // 统计当前批次结果
                let batchSuccess = 0;
                let batchFail = 0;

                result.persons.forEach((person, index) => {
                    if (person.id && person.id !== '') {
                        batchSuccess++;
                    } else {
                        batchFail++;
                        const originalData = currentBatch[index];
                        allFailedFiles.push(`${originalData.name}_${originalData.number} (${originalData.fileName}): ${person.tip || '未知错误'}`);
                    }
                });

                totalSuccess += batchSuccess;
                totalFail += batchFail;

                // 更新进度：当前批次处理完成
                updateBatchUI(
                    end,
                    personsData.length,
                    totalSuccess,
                    totalFail,
                    skipCount,
                    files.length,
                    `第 ${batchIndex + 1}/${totalBatches} 批完成（成功 ${batchSuccess}/${currentBatch.length}）`
                );

            } catch (error) {
                console.error(`批次 ${batchIndex + 1} 上传失败:`, error);
                // 标记整批为失败
                totalFail += currentBatch.length;
                currentBatch.forEach(item => {
                    allFailedFiles.push(`${item.name}_${item.number} (${item.fileName}): 批次上传失败 - ${error.message}`);
                });

                updateBatchUI(
                    end,
                    personsData.length,
                    totalSuccess,
                    totalFail,
                    skipCount,
                    files.length,
                    `第 ${batchIndex + 1}/${totalBatches} 批失败：${error.message}`
                );
            }

            // 添加小延迟，避免请求过快
            if (batchIndex < totalBatches - 1) {
                await new Promise(resolve => setTimeout(resolve, 100));
            }
        }

        // 最终结果显示
        updateBatchUI(
            personsData.length,
            personsData.length,
            totalSuccess,
            totalFail,
            skipCount,
            files.length,
            `全部完成！成功 ${totalSuccess}，失败 ${totalFail}，跳过 ${skipCount}`
        );

        // 显示最终提示
        if (totalFail === 0 && invalidFiles.length === 0) {
            showAlert(`批量入库完成！成功入库 ${totalSuccess}/${personsData.length} 张图片。`, 'success', alertBoxId);
        } else {
            let message = `批量入库完成：成功 ${totalSuccess}`;
            if (totalFail > 0) message += `，失败 ${totalFail}`;
            if (skipCount > 0) message += `，跳过 ${skipCount} 个文件`;
            showAlert(message, 'warning', alertBoxId);
        }

        // 显示跳过的文件
        if (invalidFiles.length > 0) {
            showAlert(`跳过的文件（格式错误）：<br>${invalidFiles.slice(0, 10).join('<br>')}${invalidFiles.length > 10 ? '<br>...等共 ' + invalidFiles.length + ' 个' : ''}`, 'info', alertBoxId);
        }

        // 显示失败的文件
        if (allFailedFiles.length > 0) {
            showAlert(`失败的文件：<br>${allFailedFiles.slice(0, 10).join('<br>')}${allFailedFiles.length > 10 ? '<br>...等共 ' + allFailedFiles.length + ' 个' : ''}`, 'danger', alertBoxId);
        }

        // 刷新人物列表
        loadPersons();

    } catch (error) {
        console.error('批量上传失败:', error);
        showAlert('批量上传失败: ' + error.message, 'danger', alertBoxId);
    }
}


async function handleSearchPerson(event) {
    event.preventDefault();

    const name = document.getElementById('search-name').value;
    const number = document.getElementById('search-id').value;

    if (!name && !number) {
        showAlert('请至少输入姓名或编号', 'warning', 'alert-container-add');
        return;
    }

    try {
        const requestBody = {
            name: name || '',
            number: number || ''
        };

        // 使用 POST 方法发送请求体
        const response = await fetch('/persons/search', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestBody)
        });

        const result = await response.json();

        if (!response.ok) {
            showAlert(result.detail || '查询失败', 'danger', 'alert-container-add');
            return;
        }

        const resultsDiv = document.getElementById('search-results');
        resultsDiv.innerHTML = '';

        // 后端返回的是 { persons: [...] } 格式（支持多个结果）
        if (result.persons && result.persons.length > 0) {
            result.persons.forEach(person => {
                const personCard = document.createElement('div');
                personCard.classList.add('col');
                personCard.innerHTML = `
                    <div class="card">
                        <img src="${person.photo_path || '/static/images/default.jpg'}" class="card-img-top" alt="${person.name}">
                        <div class="card-body">
                            <h6 class="card-title">${person.name}</h6>
                            <p class="card-text small">编号: ${person.number || '无'}</p>
                            <button class="btn btn-danger btn-sm" onclick="showDeleteModal('${person.id}', '${person.name}')">删除</button>
                        </div>
                    </div>
                `;
                resultsDiv.appendChild(personCard);
            });
        } else {
            showAlert('未找到匹配的人物', 'info', 'alert-container-add');
        }
    } catch (error) {
        console.error('Error searching persons:', error);
        showAlert('查询失败: ' + error.message, 'danger', 'alert-container-add');
    }
}
