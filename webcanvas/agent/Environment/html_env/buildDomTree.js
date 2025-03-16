() => {
    // Helper function to generate XPath as a tree
    function getXPathTree(element, stopAtBoundary = true) {
        const segments = [];
        let currentElement = element;

        while (currentElement && currentElement.nodeType === Node.ELEMENT_NODE) {
            // Stop if we hit a shadow root or iframe
            if (stopAtBoundary && (currentElement.parentNode instanceof ShadowRoot || currentElement.parentNode instanceof HTMLIFrameElement)) {
                break;
            }

            let index = 0;
            let sibling = currentElement.previousSibling;
            while (sibling) {
                if (sibling.nodeType === Node.ELEMENT_NODE &&
                    sibling.nodeName === currentElement.nodeName) {
                    index++;
                }
                sibling = sibling.previousSibling;
            }

            const tagName = currentElement.nodeName.toLowerCase();
            const xpathIndex = index > 0 ? `[${index + 1}]` : '';
            segments.unshift(`${tagName}${xpathIndex}`);

            currentElement = currentElement.parentNode;
        }

        return segments.join('/');
    }

    //Helper function to escape CSS identifier
    function escapeCSSIdentifier(identifier) {
        if (!identifier) return identifier;

        //escape first digit
        let escaped = identifier.replace(/^(\d)/, '\\3$1 ');
        //escape special characters
        escaped = escaped.replace(/[!"#$%&'()*+,./:;<=>?@[\\\]^`{|}~]/g, '\\$&');
        //escape leading dash
        escaped = escaped.replace(/^-/, '\\-');

        return escaped;
    }

    // Helper function to get selector
    function getSelector(element) {
        if (!element || element.nodeType !== Node.ELEMENT_NODE) return null;
    
        let selectorParts = [];
        let currentElement = element;
    
        while (currentElement && currentElement.nodeType === Node.ELEMENT_NODE) {
            let part = currentElement.tagName.toLowerCase();
    
            // Use ID if available
            if (currentElement.id) {
                part = `#${escapeCSSIdentifier(currentElement.id)}`;
                selectorParts.unshift(part);
                break;
            }
    
            // Use class names if available
            const className = currentElement.className.trim();
            if (className) {
                const classes = className.split(/\s+/)
                    .map(cls => escapeCSSIdentifier(cls))
                    .sort()
                    .join('.');
                if (classes) {
                    part += `.${classes}`;
                }
            }
    
            // Check if parent exists and has multiple children
            if (currentElement.parentElement && currentElement.parentElement.children.length > 1) {
                // Calculate sibling index as the order among all parent's children
                let siblingIndex = Array.from(currentElement.parentElement.children).indexOf(currentElement) + 1;
                // Add :nth-child
                part += `:nth-child(${siblingIndex})`;
            }
    
            selectorParts.unshift(part);
            currentElement = currentElement.parentElement;
        }
    
        return selectorParts.join(' > ');
    }

    // Helper function to check if element is accepted
    function isElementAccepted(element) {
        const leafElementDenyList = new Set(['svg', 'script', 'style', 'link', 'meta']);
        return !leafElementDenyList.has(element.tagName.toLowerCase());
    }

    // Helper function to check if element is interactive
    function isInteractiveElement(element) {
        // Base interactive elements and roles
        const interactiveElements = new Set([
            'a', 'button', 'details', 'embed', 'input', 'label',
            'menu', 'menuitem', 'object', 'select', 'textarea', 'summary'
        ]);

        const interactiveRoles = new Set([
            'button', 'menu', 'menuitem', 'link', 'checkbox', 'radio',
            'slider', 'tab', 'tabpanel', 'textbox', 'combobox', 'grid',
            'listbox', 'option', 'progressbar', 'scrollbar', 'searchbox',
            'switch', 'tree', 'treeitem', 'spinbutton', 'tooltip', 'a-button-inner', 'a-dropdown-button', 'click',
            'menuitemcheckbox', 'menuitemradio', 'a-button-text', 'button-text', 'button-icon', 'button-icon-only', 'button-text-icon-only', 'dropdown', 'combobox' 
        ]);

        const tagName = element.tagName.toLowerCase();
        const role = element.getAttribute('role');
        const ariaRole = element.getAttribute('aria-role');
        const tabIndex = element.getAttribute('tabindex');

        // Basic role/attribute checks
        const hasInteractiveRole = interactiveElements.has(tagName) ||
            interactiveRoles.has(role) ||
            interactiveRoles.has(ariaRole) ||
            (tabIndex !== null && tabIndex !== '-1') ||
            element.getAttribute('data-action') === 'a-dropdown-select' ||
            element.getAttribute('data-action') === 'a-dropdown-button';

        if (hasInteractiveRole) return true;

        // Get computed style
        const style = window.getComputedStyle(element);

        // Check if element has click-like styling
        // const hasClickStyling = style.cursor === 'pointer' ||
        //     element.style.cursor === 'pointer' ||
        //     style.pointerEvents !== 'none';

        // Check for event listeners
        const hasClickHandler = element.onclick !== null ||
            element.getAttribute('onclick') !== null ||
            element.hasAttribute('ng-click') ||
            element.hasAttribute('@click') ||
            element.hasAttribute('v-on:click');

        // Helper function to safely get event listeners
        function getEventListeners(el) {
            try {
                // Try to get listeners using Chrome DevTools API
                return window.getEventListeners?.(el) || {};
            } catch (e) {
                // Fallback: check for common event properties
                const listeners = {};

                // List of common event types to check
                const eventTypes = [
                    'click', 'mousedown', 'mouseup',
                    'touchstart', 'touchend',
                    'keydown', 'keyup', 'focus', 'blur'
                ];

                for (const type of eventTypes) {
                    const handler = el[`on${type}`];
                    if (handler) {
                        listeners[type] = [{
                            listener: handler,
                            useCapture: false
                        }];
                    }
                }

                return listeners;
            }
        }

        // Check for click-related events on the element itself
        const listeners = getEventListeners(element);
        const hasClickListeners = listeners && (
            listeners.click?.length > 0 ||
            listeners.mousedown?.length > 0 ||
            listeners.mouseup?.length > 0 ||
            listeners.touchstart?.length > 0 ||
            listeners.touchend?.length > 0
        );

        // Check for ARIA properties that suggest interactivity
        const hasAriaProps = element.hasAttribute('aria-expanded') ||
            element.hasAttribute('aria-pressed') ||
            element.hasAttribute('aria-selected') ||
            element.hasAttribute('aria-checked');

        // Check for form-related functionality
        const isFormRelated = element.form !== undefined ||
            element.hasAttribute('contenteditable') ||
            style.userSelect !== 'none';

        // Check if element is draggable
        const isDraggable = element.draggable ||
            element.getAttribute('draggable') === 'true';

        return hasAriaProps ||
            // hasClickStyling ||
            hasClickHandler ||
            hasClickListeners ||
            // isFormRelated ||
            isDraggable;

    }

    // Helper function to check if element is visible
    function isElementVisible(element) {
        const style = window.getComputedStyle(element);
        const beforeContent = window.getComputedStyle(element, '::before').content;
        const afterContent = window.getComputedStyle(element, '::after').content;

        const hasPseudoContent = (beforeContent && beforeContent !== 'none') || (afterContent && afterContent !== 'none');

        return (style.visibility !== 'hidden' && style.display !== 'none') || hasPseudoContent;
    }

    // Helper function to check if element occupies no space
    function Element_occupy_no_space(element) {
        return (element.offsetWidth === 0 && element.offsetHeight === 0) 
    }

    // Helper function to check if text node is visible
    function isTextNodeVisible(textNode) {
        const range = document.createRange();
        range.selectNodeContents(textNode);
        const rect = range.getBoundingClientRect();

        return rect.width !== 0 &&
            rect.height !== 0 &&
            // rect.top >= 0 &&
            // rect.top <= window.innerHeight &&
            textNode.parentElement?.checkVisibility({
                checkOpacity: true,
                checkVisibilityCSS: true
            });
    }

    // Process text node
    function processTextNode(node) {
        const textContent = node.textContent.trim();
        if (!textContent || !isTextNodeVisible(node)) return null;

        // Find nearest interactive parent
        const clickableParent = findNearestInteractiveParent(node);
        const selector = clickableParent ? getSelector(clickableParent) : null;

        // Get parent element attributes
        const parentAttributes = getElementAttributes(node.parentElement);

        return {
            index: ++ID.current,
            type: "TEXT_NODE",
            tagName: node.parentElement?.tagName.toLowerCase() || "",
            text: textContent,
            xpath: getXPathTree(node.parentElement, true),
            selector: selector,
            isVisible: true,
            attributes: parentAttributes,
        };
    }

    // Find nearest interactive parent
    function findNearestInteractiveParent(node) {
        let parent = node.parentElement;
        while (parent && !isInteractiveElement(parent)) {
            parent = parent.parentElement;
        }
        return parent;
    }

    // Get element attributes
    function getElementAttributes(element) {
        if (!element) return {};
        
        const attributes = {};
        const attributeNames = element.getAttributeNames?.() || [];
        for (const name of attributeNames) {
            attributes[name] = element.getAttribute(name);
        }
        return attributes;
    }

    // Process pseudo elements
    function processPseudoElements(node) {
        if (node.nodeType !== Node.ELEMENT_NODE) return {};

        const pseudoElements = {};
        const beforeStyle = window.getComputedStyle(node, '::before');
        const afterStyle = window.getComputedStyle(node, '::after');

        if (beforeStyle.content && beforeStyle.content !== 'none') {
            pseudoElements.before = {
                content: beforeStyle.content,
                style: beforeStyle.cssText
            };
        }

        if (afterStyle.content && afterStyle.content !== 'none') {
            pseudoElements.after = {
                content: afterStyle.content,
                style: afterStyle.cssText
            };
        }

        return pseudoElements;
    }

    // Build DOM tree
    function buildDomTree(node, parentIframe = null) {
        if (!node) return null;

        // Process text node
        if (node.nodeType === Node.TEXT_NODE) {
            const textNodeData = processTextNode(node);
            if (textNodeData) {
                DOM_HASH_MAP[textNodeData.index] = textNodeData;
                return textNodeData.index;
            }
            return null;
        }

        // Process element node
        if (node.nodeType !== Node.ELEMENT_NODE || !isElementAccepted(node) || !isElementVisible(node)) {
            return null;
        }

        const nodeData = {
            index: ++ID.current,
            type: "ELEMENT_NODE",
            tagName: node.tagName?.toLowerCase() || "",
            text: "",
            attributes: getElementAttributes(node),
            xpath: getXPathTree(node, true),
            selector: getSelector(node),
            children: [],
            isVisible: !Element_occupy_no_space(node),
            pseudoElements: processPseudoElements(node)
        };

        // Process Shadow DOM
        if (node.shadowRoot) {
            nodeData.shadowRoot = true;
            const shadowChildren = processChildNodes(node.shadowRoot.childNodes, parentIframe);
            nodeData.children.push(...shadowChildren);
        }

        // Process iframe
        if (node.tagName === 'IFRAME') {
            processIframeContent(node, nodeData, parentIframe);
        } else {
            const children = processChildNodes(node.childNodes, parentIframe);
            nodeData.children.push(...children);
        }

        DOM_HASH_MAP[nodeData.index] = nodeData;
        return nodeData.index;
    }

    // Process child nodes
    function processChildNodes(childNodes, parentIframe) {
        return Array.from(childNodes)
            .map(child => buildDomTree(child, parentIframe))
            .filter(Boolean);
    }

    // Process iframe content
    function processIframeContent(iframe, nodeData, parentIframe) {
        try {
            const iframeDoc = iframe.contentDocument || iframe.contentWindow?.document;
            if (iframeDoc?.body) {
                const iframeChildren = processChildNodes(iframeDoc.body.childNodes, iframe);
                nodeData.children.push(...iframeChildren);
            }
        } catch (e) {
            console.warn('Unable to access iframe:', iframe);
        }
    }

    const DOM_HASH_MAP = {};
    const ID = { current: -1 };
    const root = buildDomTree(document.body);
    return {root, map: DOM_HASH_MAP};
}